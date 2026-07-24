#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-predicted-latency.sh
# -----------------------------------------------------------------------------
# Validates the predicted-latency-routing guide.
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

# ── Shared functions ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURL_POD_NAME="curl-pl-${RANDOM}-$$"
# shellcheck source=lib-e2e-common.sh
source "${SCRIPT_DIR}/lib-e2e-common.sh"
trap cleanup_curl_pod EXIT

# ── Discover EPP service and model ─────────────────────────────────────────
discover_epp_service
setup_curl_pod
discover_model

echo "Namespace=$NAMESPACE Gateway=${SVC_HOST} EPP=${EPP_METRICS_URL} Model=${MODEL_ID} Iterations=${ITERATIONS} Concurrency=${CONCURRENCY}"

# ── Warmup loop: feed the predictor's training window ───────────────────────
PAYLOAD=$(printf '{"model":"%s","prompt":"Tell me a short story.","max_tokens":32}' "$MODEL_ID")
printf '%s' "$PAYLOAD" | kubectl exec -i -n "$NAMESPACE" "$CURL_POD_NAME" -- tee /tmp/payload.json >/dev/null

echo "Sending $ITERATIONS requests with concurrency $CONCURRENCY..."
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
