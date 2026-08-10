#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-scale-event.sh
# -----------------------------------------------------------------------------
# Validates the queue-based KEDA + EPP autoscaling path beyond signal-liveness.
#
# The deploy script already gates on Fallback=False (KEDA reads a real metric,
# not spec.fallback). That proves the *wiring*, not that load actually drives a
# scaling DECISION. This script fires a concurrent burst and asserts the KEDA
# scale event fired:
#
#   baseline desiredReplicas == min  ->  burst  ->  desiredReplicas > min  (scale UP)
#                                     ->  drain  ->  desiredReplicas == min (scale DOWN)
#
# Key property: we assert the autoscaler DECISION (hpa .status.desiredReplicas),
# NOT that the new pod becomes Ready. On a contended GPU cluster the 2nd replica
# may stay Pending for lack of a free GPU — the test still passes, because we are
# verifying that the autoscaler decided to scale, not that capacity materialized.
# desiredReplicas -> N transitively proves the whole chain worked: EPP emits the
# metric -> user-workload-monitoring scrapes it -> Thanos -> KEDA authenticates
# and reads it over threshold -> HPA raises the target.
#
# We drive the `request_running` trigger (threshold 16), not `queue_size` (>1):
# queue_size only builds when the flow-control concurrency gate closes, which the
# flow-control guide forces by sed-ing maxConcurrency 132->4; this path does not.
# running-requests climbs directly with concurrency, so it is deterministic here.
#
# Invoked by e2e-validate.sh via `--extra-validate scale-event` (which execs this
# with `-n <ns> -m <model>`). Tunables below are overridable for calibration.
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE     Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID          Model to query. If unset, auto-discovers.
  -c, --concurrency N           Parallel in-flight requests during the burst (default: 30)
  -N, --total N                 Total requests queued for the burst (default: 400)
  -t, --max-tokens N            max_tokens per request — keeps each request in
                                flight long enough to hold running-requests high (default: 512)
  -e, --epp-host HOST           EPP service host (default: \$GATEWAY_HOST, else auto-discover)
  --up-timeout SECONDS          Max wait for scale-up decision (default: 120)
  --down-timeout SECONDS        Max wait for scale-down decision (default: 180)
  -v, --verbose                 Verbose mode
  -h, --help                    Show help
EOF
  exit 0
}

NAMESPACE="llm-d"
CLI_MODEL_ID=""
CONCURRENCY="${SCALE_BURST_CONCURRENCY:-30}"
TOTAL="${SCALE_BURST_TOTAL:-400}"
MAX_TOKENS="${SCALE_BURST_MAX_TOKENS:-512}"
EPP_HOST_OVERRIDE="${GATEWAY_HOST:-}"
UP_TIMEOUT="${SCALE_UP_TIMEOUT:-120}"
DOWN_TIMEOUT="${SCALE_DOWN_TIMEOUT:-180}"
POLL_INTERVAL="${SCALE_POLL_INTERVAL:-5}"
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace)    NAMESPACE="$2"; shift 2 ;;
    -m|--model)        CLI_MODEL_ID="$2"; shift 2 ;;
    -c|--concurrency)  CONCURRENCY="$2"; shift 2 ;;
    -N|--total)        TOTAL="$2"; shift 2 ;;
    -t|--max-tokens)   MAX_TOKENS="$2"; shift 2 ;;
    -e|--epp-host)     EPP_HOST_OVERRIDE="$2"; shift 2 ;;
    --up-timeout)      UP_TIMEOUT="$2"; shift 2 ;;
    --down-timeout)    DOWN_TIMEOUT="$2"; shift 2 ;;
    -v|--verbose)      VERBOSE=true; shift ;;
    -h|--help)         show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

[[ "${VERBOSE}" == "true" ]] && set -x

CURL_POD_NAME="curl-scale-${RANDOM}-$$"
BURST_PID=""

cleanup() {
  [[ -n "${BURST_PID}" ]] && kill "${BURST_PID}" 2>/dev/null || true
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Discover the ScaledObject, its target Deployment, and the KEDA HPA ───────
SCALEDOBJECT=$(kubectl get scaledobject -n "$NAMESPACE" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -z "$SCALEDOBJECT" ]]; then
  echo "Error: no ScaledObject found in namespace '$NAMESPACE'." >&2
  exit 1
fi
DEPLOY=$(kubectl get scaledobject "$SCALEDOBJECT" -n "$NAMESPACE" \
  -o jsonpath='{.spec.scaleTargetRef.name}' 2>/dev/null || true)
# The HPA KEDA manages is the one whose scaleTargetRef points at that Deployment.
HPA=$(kubectl get hpa -n "$NAMESPACE" \
  -o jsonpath="{range .items[?(@.spec.scaleTargetRef.name=='${DEPLOY}')]}{.metadata.name}{end}" 2>/dev/null || true)
if [[ -z "$HPA" ]]; then
  echo "Error: could not find the HPA targeting Deployment '${DEPLOY}' in '$NAMESPACE'." >&2
  kubectl get scaledobject,hpa -n "$NAMESPACE" >&2 || true
  exit 1
fi
MIN=$(kubectl get hpa "$HPA" -n "$NAMESPACE" -o jsonpath='{.spec.minReplicas}' 2>/dev/null || echo 1)
MAX=$(kubectl get hpa "$HPA" -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo 2)
MIN="${MIN:-1}"

# ── Discover the EPP service (traffic on :80) ────────────────────────────────
HOST="${EPP_HOST_OVERRIDE:-}"
if [[ -z "$HOST" ]]; then
  EPP_SVC_NAME=$(kubectl get svc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null \
    | tr ' ' '\n' | grep -E -- '-epp$' | head -1 || true)
  [[ -n "$EPP_SVC_NAME" ]] && HOST=$(kubectl get svc "$EPP_SVC_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
fi
if [[ -z "$HOST" ]]; then
  echo "Error: could not discover EPP service in '$NAMESPACE' (set GATEWAY_HOST or -e)." >&2
  exit 1
fi
SVC_HOST="${HOST}:80"

# ── curl pod ────────────────────────────────────────────────────────────────
kubectl run "$CURL_POD_NAME" --namespace "$NAMESPACE" \
  --image=curlimages/curl --restart=Never -- sleep 3600 >/dev/null
if ! kubectl wait --for=condition=Ready pod/"$CURL_POD_NAME" -n "$NAMESPACE" --timeout=120s; then
  echo "Error: curl pod failed to become ready" >&2
  kubectl describe pod -n "$NAMESPACE" "$CURL_POD_NAME" >&2 || true
  exit 1
fi

# ── Model ───────────────────────────────────────────────────────────────────
MODEL_ID="${CLI_MODEL_ID:-${MODEL_ID:-}}"
if [[ -z "$MODEL_ID" ]]; then
  for _ in $(seq 1 10); do
    MODEL_ID=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- \
      curl -sS --max-time 15 "http://${SVC_HOST}/v1/models" 2>/dev/null \
      | grep -o '"id":"[^"]*"' | head -n1 | cut -d '"' -f4 || true)
    [[ -n "$MODEL_ID" ]] && break
    sleep 10
  done
fi
[[ -z "$MODEL_ID" ]] && { echo "Error: could not resolve a model id." >&2; exit 1; }

echo "Namespace=$NAMESPACE SO=$SCALEDOBJECT Deploy=$DEPLOY HPA=$HPA min=$MIN max=$MAX"
echo "Gateway=$SVC_HOST Model=$MODEL_ID  burst: concurrency=$CONCURRENCY total=$TOTAL max_tokens=$MAX_TOKENS"

desired() { kubectl get hpa "$HPA" -n "$NAMESPACE" -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || echo ""; }
hpa_line() { kubectl get hpa "$HPA" -n "$NAMESPACE" --no-headers 2>/dev/null || true; }

# ── Baseline ────────────────────────────────────────────────────────────────
BASE=$(desired); BASE="${BASE:-$MIN}"
echo "── Baseline: desiredReplicas=${BASE} (min=${MIN}) ──"
if [[ "$BASE" -gt "$MIN" ]]; then
  echo "Error: expected baseline desiredReplicas == min (${MIN}); already at ${BASE}." >&2
  echo "       Something is holding the metric above threshold before the burst." >&2
  exit 1
fi

# ── Stage payload + fire the sustained concurrent burst ─────────────────────
PAYLOAD=$(printf '{"model":"%s","prompt":"Write a very long, detailed story about a robot that learns to paint, with rich description.","max_tokens":%s}' "$MODEL_ID" "$MAX_TOKENS")
printf '%s' "$PAYLOAD" | kubectl exec -i -n "$NAMESPACE" "$CURL_POD_NAME" -- tee /tmp/payload.json >/dev/null

echo "── Firing burst (keeps ~${CONCURRENCY} requests in flight) to drive request_running > 16 ──"
kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- sh -c "
  seq 1 ${TOTAL} | xargs -I{} -P ${CONCURRENCY} \
    curl -sS --max-time 300 -o /dev/null \
      -X POST 'http://${SVC_HOST}/v1/completions' \
      -H 'content-type: application/json' \
      --data-binary @/tmp/payload.json
" >/dev/null 2>&1 &
BURST_PID=$!

# ── Assert scale-UP: desiredReplicas rises above baseline ───────────────────
echo "── Waiting up to ${UP_TIMEOUT}s for a scale-up decision (desiredReplicas > ${MIN}) ──"
scaled_up=false
deadline=$(( SECONDS + UP_TIMEOUT ))
while (( SECONDS < deadline )); do
  d=$(desired)
  echo "  $(hpa_line)"
  if [[ -n "$d" && "$d" -gt "$MIN" ]]; then
    echo "  ✓ scale-up decision observed: desiredReplicas=${d} (HPA current metric shown as TARGETS above)"
    scaled_up=true
    break
  fi
  sleep "$POLL_INTERVAL"
done

# Stop the load hard (delete the pod → kills all in-flight curls) so scale-down can begin.
kill "${BURST_PID}" 2>/dev/null || true; BURST_PID=""
kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" --ignore-not-found --wait=false >/dev/null 2>&1 || true

if ! $scaled_up; then
  echo "❌ No scale-up: desiredReplicas stayed at ${MIN} within ${UP_TIMEOUT}s." >&2
  echo "   KEDA never saw request_running cross threshold 16. Raise --concurrency/--max-tokens" >&2
  echo "   (the burst may have drained before a KEDA poll), or check the trigger." >&2
  kubectl describe hpa "$HPA" -n "$NAMESPACE" 2>/dev/null | grep -A6 -i "events\|metrics" >&2 || true
  exit 1
fi

# ── Assert scale-DOWN: desiredReplicas returns to baseline ──────────────────
# The nightly overlay lowers scaleDown.stabilizationWindowSeconds (300 -> ~30-60s)
# so this returns quickly; see nightly-deploy-ocp-keda-epp.sh. This does NOT need
# the 2nd pod to have become Ready — it is metric + window driven.
echo "── Waiting up to ${DOWN_TIMEOUT}s for a scale-down decision (desiredReplicas == ${MIN}) ──"
scaled_down=false
deadline=$(( SECONDS + DOWN_TIMEOUT ))
while (( SECONDS < deadline )); do
  d=$(desired)
  echo "  $(hpa_line)"
  if [[ -n "$d" && "$d" -le "$MIN" ]]; then
    echo "  ✓ scale-down decision observed: desiredReplicas=${d}"
    scaled_down=true
    break
  fi
  sleep "$POLL_INTERVAL"
done

if ! $scaled_down; then
  echo "❌ No scale-down: desiredReplicas did not return to ${MIN} within ${DOWN_TIMEOUT}s after load stopped." >&2
  echo "   Check scaleDown.stabilizationWindowSeconds (nightly should lower it) and cooldownPeriod." >&2
  exit 1
fi

echo "✅ Scale-event verified: burst drove desiredReplicas ${MIN}→>${MIN} then back to ${MIN} — KEDA scaled on the real EPP metric (decision asserted; 2nd pod readiness intentionally not required)."
