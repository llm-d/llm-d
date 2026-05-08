#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-predicted-latency.sh
# -----------------------------------------------------------------------------
# Validates the predicted-latency-based-scheduling guide.
#
# The default e2e-validate.sh proves the gateway returns 200s, but cannot tell
# whether the predicted-latency scheduler actually used predictions or silently
# fell back to the composite KV/queue/prefix heuristic (see
# docs/wip-docs-new/architecture/advanced/latency-predictor.md — "If the
# prediction server is unreachable or fails to return a prediction, the latency
# scorer falls back ...").
#
# This script:
#   1. Sends ITERATIONS requests through the gateway (concurrent), seeding the
#      predictor's training window.
#   2. Scrapes the EPP /metrics endpoint and asserts the predicted-TTFT
#      histogram has samples — proving the predictor served predictions.
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE     Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID          Model to query. If unset, auto-discovers.
  -i, --iterations N            Total requests to send (default: 100)
  -c, --concurrency N           Parallel requests (default: 8)
  -e, --epp-host HOST           EPP service host (default: \$EPP_HOST or \$GATEWAY_HOST)
  -p, --epp-metrics-port PORT   EPP metrics port (default: 9090)
  -v, --verbose                 Verbose mode
  -h, --help                    Show help
EOF
  exit 0
}

NAMESPACE="llm-d"
CLI_MODEL_ID=""
ITERATIONS=100
CONCURRENCY=8
EPP_METRICS_PORT="${EPP_METRICS_PORT:-9090}"
EPP_HOST_OVERRIDE="${EPP_HOST:-}"
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace)        NAMESPACE="$2"; shift 2 ;;
    -m|--model)            CLI_MODEL_ID="$2"; shift 2 ;;
    -i|--iterations)       ITERATIONS="$2"; shift 2 ;;
    -c|--concurrency)      CONCURRENCY="$2"; shift 2 ;;
    -e|--epp-host)         EPP_HOST_OVERRIDE="$2"; shift 2 ;;
    -p|--epp-metrics-port) EPP_METRICS_PORT="$2"; shift 2 ;;
    -v|--verbose)          VERBOSE=true; shift ;;
    -h|--help)             show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

[[ "${VERBOSE}" == "true" ]] && set -x

CURL_POD_NAME="curl-pl-${RANDOM}-$$"
CURL_POD_TIMEOUT_SECONDS="${CURL_POD_TIMEOUT_SECONDS:-120}"

setup_curl_pod() {
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" --ignore-not-found >/dev/null 2>&1 || true
  echo "Creating curl pod ${CURL_POD_NAME}..."
  kubectl run "$CURL_POD_NAME" --namespace "$NAMESPACE" \
    --image=curlimages/curl --restart=Never -- sleep 3600 >/dev/null
  if ! kubectl wait --for=condition=Ready pod/"$CURL_POD_NAME" \
       -n "$NAMESPACE" --timeout="${CURL_POD_TIMEOUT_SECONDS}s"; then
    echo "Error: curl pod failed to become ready" >&2
    kubectl describe pod -n "$NAMESPACE" "$CURL_POD_NAME" >&2 || true
    exit 1
  fi
}

cleanup_curl_pod() {
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup_curl_pod EXIT

run_curl() {
  CURL_OUTPUT=""; CURL_EXIT=0
  CURL_OUTPUT=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- "$@" 2>&1) || CURL_EXIT=$?
}

# ── Discover EPP service ────────────────────────────────────────────────────
# The predicted-latency guide deploys the llm-d Router in standalone mode —
# there is no Gateway resource. Both the request flow (port 80, Envoy sidecar)
# and the metrics endpoint (port 9090) are served by the EPP service itself,
# so we resolve directly to the EPP service's ClusterIP.
#
# Multi-replica caveat: when more than one EPP pod is running, /metrics is
# forwarded to one pod at random per scrape, so the histogram counts reflect
# only that pod's view. base.values.yaml currently sets replicas: 1 so this
# is fine; future scale-out will need per-pod scraping (port-forward or
# headless service).
HOST="${GATEWAY_HOST:-}"
if [[ -z "$HOST" ]]; then
  EPP_SVC_NAME=$(kubectl get svc -n "$NAMESPACE" \
      -o jsonpath='{.items[*].metadata.name}' 2>/dev/null \
      | tr ' ' '\n' | grep -E -- '-epp$' | head -1 || true)
  if [[ -n "$EPP_SVC_NAME" ]]; then
    HOST=$(kubectl get svc "$EPP_SVC_NAME" -n "$NAMESPACE" \
      -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  fi
fi
if [[ -z "$HOST" ]]; then
  echo "Error: could not discover EPP service in namespace '$NAMESPACE'." >&2
  echo "       Set GATEWAY_HOST env var to the EPP service name or ClusterIP." >&2
  exit 1
fi
SVC_HOST="${HOST}:80"
EPP_HOST="${EPP_HOST_OVERRIDE:-$HOST}"
EPP_METRICS_URL="http://${EPP_HOST}:${EPP_METRICS_PORT}/metrics"

setup_curl_pod

# ── Discover model ──────────────────────────────────────────────────────────
if [[ -n "$CLI_MODEL_ID" ]]; then
  MODEL_ID="$CLI_MODEL_ID"
elif [[ -n "${MODEL_ID-}" ]]; then
  : # already set in env
else
  echo "Auto-discovering model from ${SVC_HOST}/v1/models..."
  MODEL_ID=""
  for _ in $(seq 1 10); do
    run_curl curl -sS --max-time 15 "http://${SVC_HOST}/v1/models" || true
    MODEL_ID=$(echo "${CURL_OUTPUT}" | grep -o '"id":"[^"]*"' | head -n 1 | cut -d '"' -f 4) || true
    [[ -n "$MODEL_ID" ]] && break
    sleep 10
  done
  if [[ -z "$MODEL_ID" ]]; then
    echo "Error: could not auto-discover model after 10 attempts." >&2
    exit 1
  fi
fi

echo "Namespace=$NAMESPACE Gateway=${SVC_HOST} EPP=${EPP_METRICS_URL} Model=${MODEL_ID} Iterations=${ITERATIONS} Concurrency=${CONCURRENCY}"

# ── Warmup loop: feed the predictor's training window ───────────────────────
PAYLOAD=$(printf '{"model":"%s","prompt":"Tell me a short story.","max_tokens":32}' "$MODEL_ID")
# Pipe via tee so the payload actually lands in the file. `kubectl exec` +
# `sh -c "cat > file"` silently produces a 0-byte file (cat's stdin never gets
# the data through sh -c, even with -i), which would make every warmup curl
# send an empty body and report ok=0 fail=N.
printf '%s' "$PAYLOAD" | kubectl exec -i -n "$NAMESPACE" "$CURL_POD_NAME" -- tee /tmp/payload.json >/dev/null

echo "Sending $ITERATIONS requests with concurrency $CONCURRENCY..."
# Tolerate partial failures: a single curl that fails to connect would otherwise
# propagate up through xargs/kubectl exec under `set -e` and abort the script
# before we get to the ok/fail accounting and the metrics check below.
status_log=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- sh -c "
  seq 1 $ITERATIONS | xargs -I{} -P $CONCURRENCY \
    curl -sS --max-time 60 -o /dev/null -w '%{http_code}\n' \
      -X POST 'http://${SVC_HOST}/v1/completions' \
      -H 'content-type: application/json' \
      --data-binary @/tmp/payload.json
" || true)

ok=$(echo "$status_log" | grep -c '^200$' || true)
fail=$(echo "$status_log" | grep -cv '^200$' || true)
echo "Warmup result: ok=$ok fail=$fail"
if [[ "$ok" -eq 0 ]]; then
  echo "Error: zero successful warmup requests — gateway/model server is not serving traffic." >&2
  exit 1
fi

# ── Scrape EPP /metrics ─────────────────────────────────────────────────────
echo "Scraping EPP metrics at ${EPP_METRICS_URL}..."
run_curl curl -sS --max-time 15 "${EPP_METRICS_URL}"
metrics="$CURL_OUTPUT"
if [[ "$CURL_EXIT" -ne 0 || -z "$metrics" ]]; then
  echo "Error: failed to scrape ${EPP_METRICS_URL} (exit $CURL_EXIT)" >&2
  echo "$metrics" >&2
  exit 1
fi

# Sum *_count samples across all label combinations for a histogram series.
sum_histogram_count() {
  echo "$metrics" \
    | awk -v series="${1}_count" '
        $1 ~ "^"series"(\\{|$)" { gsub(/[^0-9.eE+-]/, "", $NF); sum += $NF }
        END { printf("%d\n", (sum=="" ? 0 : sum)) }
      '
}

ACTUAL=$(sum_histogram_count inference_objective_request_ttft_seconds)
PREDICTED=$(sum_histogram_count inference_objective_request_predicted_ttft_seconds)

echo "actual_ttft_count=${ACTUAL} predicted_ttft_count=${PREDICTED}"

if [[ "$ACTUAL" -eq 0 ]]; then
  echo "Error: actual TTFT histogram is empty — request flow itself didn't reach the EPP." >&2
  exit 1
fi
if [[ "$PREDICTED" -eq 0 ]]; then
  echo "Error: predicted TTFT histogram is empty after ${ITERATIONS} requests." >&2
  echo "       The scheduler likely fell back to the composite KV/queue/prefix heuristic." >&2
  echo "       Inspect EPP logs for 'prediction server unreachable' or training-server errors." >&2
  exit 1
fi

echo "✅ Predicted-latency scheduling is active: predictor returned predictions for ${PREDICTED} requests."
