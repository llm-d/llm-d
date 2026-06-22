#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-flow-control.sh
# -----------------------------------------------------------------------------
# Validates the flow-control guide beyond the default smoke test.
#
# The smoke loop sends single sequential requests, but flow control is
# work-conserving — those never queue, so they never exercise the feature. This
# script forces contention and checks the three things flow control promises:
# backpressure, per-band queue isolation, and priority-based QoS.
#
# Determinism hinges on CI deploying the guide with a low concurrency-detector
# maxConcurrency (the nightly seds 132 -> 4): with the gate closing under a
# modest burst, the assertions become exact rather than racy.
#
# Scope: this proves priority *ordering*. Realistic weighted multi-tenant
# fairness/SLO at scale needs inference-perf multi-tenant trace replay (the
# heavy benchmark tier; see the guide's Benchmarking section).
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE     Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID          Model to query. If unset, auto-discovers.
  -b, --burst N                 Concurrent requests per band (default: 12)
  -t, --max-tokens N            max_tokens per request, keeps the gate closed (default: 96)
  -e, --epp-host HOST           EPP service host (default: \$EPP_HOST or \$GATEWAY_HOST)
  -p, --epp-metrics-port PORT   EPP metrics port (default: 9090)
  -v, --verbose                 Verbose mode
  -h, --help                    Show help
EOF
  exit 0
}

NAMESPACE="llm-d"
CLI_MODEL_ID=""
BURST=12
MAX_TOKENS=96
POLL_ATTEMPTS="${POLL_ATTEMPTS:-12}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-2}"
EPP_METRICS_PORT="${EPP_METRICS_PORT:-9090}"
EPP_HOST_OVERRIDE="${EPP_HOST:-}"
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace)        NAMESPACE="$2"; shift 2 ;;
    -m|--model)            CLI_MODEL_ID="$2"; shift 2 ;;
    -b|--burst)            BURST="$2"; shift 2 ;;
    -t|--max-tokens)       MAX_TOKENS="$2"; shift 2 ;;
    -e|--epp-host)         EPP_HOST_OVERRIDE="$2"; shift 2 ;;
    -p|--epp-metrics-port) EPP_METRICS_PORT="$2"; shift 2 ;;
    -v|--verbose)          VERBOSE=true; shift ;;
    -h|--help)             show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

[[ "${VERBOSE}" == "true" ]] && set -x

CURL_POD_NAME="curl-fc-${RANDOM}-$$"
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
# Standalone mode has no Gateway resource: the EPP service serves both traffic
# (:80) and metrics (:9090), so resolve straight to its ClusterIP.
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
# NOTE: assumes a single EPP replica (base.values.yaml sets replicas: 1). With
# more, /metrics load-balances per scrape and the gauges below go inconsistent.

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

echo "Namespace=$NAMESPACE Gateway=${SVC_HOST} EPP=${EPP_METRICS_URL} Model=${MODEL_ID} Burst/band=${BURST} max_tokens=${MAX_TOKENS}"

# ── Stage the request payload on the curl pod ───────────────────────────────
# max_tokens is sized to keep each request in flight long enough to hold the
# gate closed while the queue fills. Stage via `tee`: `kubectl exec` + redirect
# silently writes a 0-byte file, sending empty request bodies.
PAYLOAD=$(printf '{"model":"%s","prompt":"Write a long detailed story about a robot learning to paint.","max_tokens":%s}' "$MODEL_ID" "$MAX_TOKENS")
printf '%s' "$PAYLOAD" | kubectl exec -i -n "$NAMESPACE" "$CURL_POD_NAME" -- tee /tmp/payload.json >/dev/null

# ── Metric extraction helpers ───────────────────────────────────────────────
# Sum a series' values across every label set carrying priority="<v>". Works
# for the queue_size gauge and the queue_duration _sum/_count families alike.
metric_sum_for_priority() {
  local series="$1" priority="$2"
  echo "$METRICS" \
    | awk -v series="$series" -v pri="priority=\"${priority}\"" '
        $0 ~ ("^"series"\\{") && index($0, pri) > 0 {
          gsub(/[^0-9.eE+-]/, "", $NF); sum += $NF
        }
        END { printf("%g\n", (sum=="" ? 0 : sum)) }
      '
}

gauge_max() {
  local series="$1"
  echo "$METRICS" \
    | awk -v series="$series" '
        $0 ~ ("^"series"(\\{|[ \t])") {
          v=$NF; gsub(/[^0-9.eE+-]/, "", v); if (v > max) max = v
        }
        END { printf("%.4f\n", (max=="" ? 0 : max)) }
      '
}

# Histogram mean = _sum / _count for a given priority; 0 when no samples.
hist_mean_for_priority() {
  local series="$1" priority="$2" s c
  s=$(metric_sum_for_priority "${series}_sum" "$priority")
  c=$(metric_sum_for_priority "${series}_count" "$priority")
  awk -v s="$s" -v c="$c" 'BEGIN { printf("%.4f\n", (c>0 ? s/c : 0)) }'
}

# float a > b
fgt() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a>b)}'; }

scrape_metrics() {
  run_curl curl -sS --max-time 15 "${EPP_METRICS_URL}"
  METRICS="$CURL_OUTPUT"
  if [[ "$CURL_EXIT" -ne 0 || -z "$METRICS" ]]; then
    echo "Warn: metrics scrape failed (exit $CURL_EXIT)" >&2
    METRICS=""
    return 1
  fi
  return 0
}

QSIZE=inference_extension_flow_control_queue_size
SAT=inference_extension_flow_control_pool_saturation
QD=inference_extension_flow_control_request_queue_duration_seconds

# Background one band's burst; FlowKey = (fairness id, objective-derived priority).
fire_band() {
  local objective="$1" priority="$2" tenant="$3"
  kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- sh -c "
    seq 1 ${BURST} | xargs -I{} -P ${BURST} \
      curl -sS --max-time 180 -o /dev/null -w '%{http_code}\n' \
        -X POST 'http://${SVC_HOST}/v1/completions' \
        -H 'content-type: application/json' \
        -H 'x-llm-d-inference-fairness-id: ${tenant}' \
        -H 'x-llm-d-inference-objective: ${objective}' \
        --data-binary @/tmp/payload.json
  " >"/tmp/burst-${priority}.log" 2>&1 &
  echo $!
}

# ── Mixed-contention burst: all three bands at once ─────────────────────────
# Firing every band simultaneously is what surfaces QoS: under a closed gate the
# hardcoded strict-priority dispatcher must drain premium before best-effort.
echo "── Firing ${BURST} requests into each band simultaneously (premium=100, standard=0, best-effort=-10) ──"
PID_PREMIUM=$(fire_band premium-traffic     100 tenant-a)
PID_STANDARD=$(fire_band standard-traffic   0   tenant-b)
PID_BEST=$(fire_band best-effort-traffic    -10 tenant-c)

peak_total_q=0
peak_sat=0
drain_order_observed=false
seen_premium_queued=false

for _ in $(seq 1 "$POLL_ATTEMPTS"); do
  if scrape_metrics; then
    q100=$(metric_sum_for_priority "$QSIZE" 100)
    q0=$(metric_sum_for_priority "$QSIZE" 0)
    qbe=$(metric_sum_for_priority "$QSIZE" -10)
    sat=$(gauge_max "$SAT")
    total=$(awk -v a="$q100" -v b="$q0" -v c="$qbe" 'BEGIN{printf("%d", a+b+c)}')
    (( total > peak_total_q )) && peak_total_q=$total
    fgt "$sat" "$peak_sat" && peak_sat=$sat
    fgt "$q100" 0 && seen_premium_queued=true
    # Premium emptied while best-effort still waits == strict priority in action.
    if $seen_premium_queued && [[ "$q100" == "0" ]] && fgt "$qbe" 0; then
      drain_order_observed=true
    fi
    echo "  poll: queue_size premium=${q100} standard=${q0} best-effort=${qbe} | pool_saturation(max)=${sat}"
  fi
  if ! kill -0 "$PID_PREMIUM" 2>/dev/null && ! kill -0 "$PID_STANDARD" 2>/dev/null \
     && ! kill -0 "$PID_BEST" 2>/dev/null; then
    break
  fi
  sleep "$POLL_INTERVAL_SECONDS"
done

wait "$PID_PREMIUM" "$PID_STANDARD" "$PID_BEST" 2>/dev/null || true
echo "  burst response codes:"
for pri in 100 0 -10; do
  printf '    priority=%s: ' "$pri"
  sort "/tmp/burst-${pri}.log" 2>/dev/null | uniq -c | tr '\n' ' '; echo
done

scrape_metrics || { echo "Error: final metrics scrape failed." >&2; exit 1; }

fail=false

# priority -> human name. A case fn (not a bash-4 `declare -A`) keeps this
# runnable on macOS bash 3.2 and avoids the negative-key subscript pitfall.
band_name() { case "$1" in 100) echo premium ;; 0) echo standard ;; -10) echo best-effort ;; *) echo "band-$1" ;; esac; }

# Backpressure signal — informational only. queue_size and pool_saturation are
# gauges sampled mid-burst, so a fast drain can leave the peak between polls.
# Backpressure is instead proven cumulatively by the QoS gap below: best-effort
# could only out-wait premium if the queue actually held it back.
echo "── Backpressure (peak gauges, informational) ──"
echo "  peak total queue_size=${peak_total_q}  peak pool_saturation=${peak_sat}"
$drain_order_observed && echo "  observed live drain order: premium emptied while best-effort still queued."

# ── (1) Each band landed in its own queue ───────────────────────────────────
# queue_duration_count is cumulative — unlike the gauges above it cannot be
# missed by scrape timing, so it is the robust backbone assertion.
echo "── Band classification (queue_duration_count) ──"
for pri in 100 0 -10; do
  c=$(metric_sum_for_priority "${QD}_count" "$pri")
  echo "  $(band_name "$pri") (priority=${pri}): count=${c}"
  if ! fgt "$c" 0; then
    echo "Error: queue-duration histogram empty for the $(band_name "$pri") band (priority=${pri})." >&2
    echo "       Either the band's traffic bypassed flow control, or objectives.yaml is" >&2
    echo "       not applied so '$(band_name "$pri")' resolved to the default band 0." >&2
    fail=true
  fi
done

# ── (2) QoS: best-effort waits longer than premium under contention ─────────
# Compare only the extremes — the premium/standard gap is small enough to be
# noise when the pool drains fast, so asserting it would flake. A nonzero gap
# also doubles as the cumulative proof that backpressure engaged at all.
echo "── QoS differentiation (mean queue wait, seconds) ──"
MEAN_PREMIUM=$(hist_mean_for_priority "$QD" 100)
MEAN_STANDARD=$(hist_mean_for_priority "$QD" 0)
MEAN_BEST=$(hist_mean_for_priority "$QD" -10)
echo "  premium=${MEAN_PREMIUM}  standard=${MEAN_STANDARD}  best-effort=${MEAN_BEST}"
# 50ms floor guards against asserting on float noise when waits are near zero.
QOS_GAP=$(awk -v be="$MEAN_BEST" -v pr="$MEAN_PREMIUM" 'BEGIN{printf("%.4f", be-pr)}')
if ! fgt "$QOS_GAP" 0.05; then
  echo "Error: no QoS differentiation — best-effort mean wait (${MEAN_BEST}s) did not exceed" >&2
  echo "       premium mean wait (${MEAN_PREMIUM}s) by a meaningful margin (gap=${QOS_GAP}s)." >&2
  echo "       Either backpressure never engaged (was maxConcurrency lowered?) or strict" >&2
  echo "       priority is not ordering the bands." >&2
  fail=true
else
  echo "  ✓ best-effort waited ${QOS_GAP}s longer than premium (strict priority honored)."
fi

if $fail; then
  echo "❌ Flow control validation failed." >&2
  exit 1
fi

echo "✅ Flow control verified: each band queued independently and higher priority was served first under contention (best-effort waited ${QOS_GAP}s longer than premium)."
