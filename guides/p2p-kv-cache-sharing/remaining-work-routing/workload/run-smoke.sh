#!/usr/bin/env bash
# Run one cache-isolated exact-token smoke through one EPP arm.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
KUBE_CONTEXT=${KUBE_CONTEXT:-kermit_US-EAST-01A}
NS=${NS:-nilig-agentx-slo}
PVC_ACCESS_POD=${PVC_ACCESS_POD:-workload-access}
REMOTE_ROOT=${REMOTE_ROOT:-/workload/p2p-context-migration-glm}
HTTP_PORT=${HTTP_PORT:-18081}
METRICS_PORT=${METRICS_PORT:-19090}
CACHE_WAIT_SECONDS=${CACHE_WAIT_SECONDS:-30}
BACKGROUND_REQUESTS=${BACKGROUND_REQUESTS:-0}
POOL_BACKGROUND_REQUESTS=${POOL_BACKGROUND_REQUESTS:-0}
MINIMUM_INFLIGHT_TOKENS=${MINIMUM_INFLIGHT_TOKENS:-12548}
TRIGGER_MINIMUM_INFLIGHT_TOKENS=${TRIGGER_MINIMUM_INFLIGHT_TOKENS:-12548}
DIRECT_BACKGROUND_REQUESTS_PER_RANK=${DIRECT_BACKGROUND_REQUESTS_PER_RANK:-0}
DIRECT_RANKS=${DIRECT_RANKS:-8}
DIRECT_PREFILL_POD=${DIRECT_PREFILL_POD:-glm-5-2-prefill-long-0}
DIRECT_HTTP_PORT_BASE=${DIRECT_HTTP_PORT_BASE:-28000}
MINIMUM_LOADED_RANKS=${MINIMUM_LOADED_RANKS:-1}
REQUIRE_CAPACITY_QUEUED_RANKS=${REQUIRE_CAPACITY_QUEUED_RANKS:-0}
LOAD_WAIT_TIMEOUT=${LOAD_WAIT_TIMEOUT:-60}
REQUEST_TIMEOUT=${REQUEST_TIMEOUT:-900}

ARM=${1:?usage: $0 baseline|candidate UNIQUE_SALT}
SALT=${2:?usage: $0 baseline|candidate UNIQUE_SALT}
case "$ARM" in
baseline) EPP=${BASELINE_EPP:-glm-cache-route-epp-baseline} ;;
candidate) EPP=${CANDIDATE_EPP:-glm-cache-route-epp-candidate} ;;
*) echo "arm must be baseline or candidate" >&2; exit 2 ;;
esac
case "$SALT" in
''|*[!0-9-]*) echo "salt must be an integer" >&2; exit 2 ;;
esac

RUN_ID=${RUN_ID:-smoke-$(date -u +%Y%m%d-%H%M%S)-${ARM}-${SALT}}
REMOTE_DIR="${REMOTE_ROOT}/${RUN_ID}"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/glm-context-smoke.XXXXXX")
PORT_FORWARD_PID=
DIRECT_PORT_FORWARD_PID=
DECISION_LOG_PID=

cleanup() {
  if [[ -n "$PORT_FORWARD_PID" ]]; then
    kill "$PORT_FORWARD_PID" 2>/dev/null || true
    wait "$PORT_FORWARD_PID" 2>/dev/null || true
  fi
  if [[ -n "$DIRECT_PORT_FORWARD_PID" ]]; then
    kill "$DIRECT_PORT_FORWARD_PID" 2>/dev/null || true
    wait "$DIRECT_PORT_FORWARD_PID" 2>/dev/null || true
  fi
  if [[ -n "$DECISION_LOG_PID" ]]; then
    kill "$DECISION_LOG_PID" 2>/dev/null || true
    wait "$DECISION_LOG_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

k() {
  kubectl --context "$KUBE_CONTEXT" "$@"
}

for command in kubectl python3 rg; do
  command -v "$command" >/dev/null || {
    echo "required command not found: $command" >&2
    exit 1
  }
done

k -n "$NS" get "svc/${EPP}" >/dev/null
k -n "$NS" wait --for=condition=Ready "pod/${PVC_ACCESS_POD}" --timeout=2m
if k -n "$NS" exec "$PVC_ACCESS_POD" -- test -e "$REMOTE_DIR"; then
  echo "artifact directory already exists: ${REMOTE_DIR}" >&2
  exit 1
fi
k -n "$NS" exec "$PVC_ACCESS_POD" -- mkdir -p "$REMOTE_DIR"

python3 "$HERE/counters.py" snapshot \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --output "$TMP_DIR/counters-pre.json"
k -n "$NS" get "svc/${EPP}" -o yaml > "$TMP_DIR/epp-service.yaml"
k -n "$NS" get "deploy/${EPP}" -o yaml > "$TMP_DIR/epp-deployment.yaml"
k -n "$NS" get pods -l 'llm-d.ai/model=GLM-5.2-FP8' -o wide \
  > "$TMP_DIR/engine-pods.txt"

START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
k -n "$NS" port-forward "svc/${EPP}" \
  "${HTTP_PORT}:80" "${METRICS_PORT}:9090" \
  > "$TMP_DIR/port-forward.log" 2>&1 &
PORT_FORWARD_PID=$!

ready=0
for _ in $(seq 1 60); do
  if python3 -c \
    "import socket; socket.create_connection(('127.0.0.1', ${HTTP_PORT}), 1).close()" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo "port-forward to svc/${EPP} did not become ready" >&2
  cat "$TMP_DIR/port-forward.log" >&2
  exit 1
fi

if [[ "$DIRECT_BACKGROUND_REQUESTS_PER_RANK" -gt 0 ]]; then
  DIRECT_MAPPINGS=()
  for rank in $(seq 0 $((DIRECT_RANKS - 1))); do
    DIRECT_MAPPINGS+=(
      "$((DIRECT_HTTP_PORT_BASE + rank)):$((8000 + rank))"
    )
  done
  k -n "$NS" port-forward "pod/${DIRECT_PREFILL_POD}" \
    "${DIRECT_MAPPINGS[@]}" \
    > "$TMP_DIR/direct-port-forward.log" 2>&1 &
  DIRECT_PORT_FORWARD_PID=$!
  direct_ready=0
  for _ in $(seq 1 60); do
    if python3 -c \
      "import socket; [socket.create_connection(('127.0.0.1', ${DIRECT_HTTP_PORT_BASE} + rank), 1).close() for rank in range(${DIRECT_RANKS})]" \
      >/dev/null 2>&1; then
      direct_ready=1
      break
    fi
    if ! kill -0 "$DIRECT_PORT_FORWARD_PID" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if [[ "$direct_ready" != 1 ]]; then
    echo "direct P-long port-forward did not become ready" >&2
    cat "$TMP_DIR/direct-port-forward.log" >&2
    exit 1
  fi
  if rg -q 'Unable to listen|error forwarding port' \
    "$TMP_DIR/direct-port-forward.log"; then
    echo "direct P-long port-forward did not bind every rank" >&2
    cat "$TMP_DIR/direct-port-forward.log" >&2
    exit 1
  fi
fi

k -n "$NS" logs -f "deploy/${EPP}" -c epp --since-time "$START_TIME" \
  2>&1 | rg --line-buffered \
    'prefixcacheaffinity|sessionaffinity|cache-load-gate|decode-session-affinity|p2psource|prefill-work-router|Selected endpoints' \
  > "$TMP_DIR/epp-decisions.log" &
DECISION_LOG_PID=$!

python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${METRICS_PORT}/metrics', timeout=10).read().decode(), end='')" \
  > "$TMP_DIR/epp-metrics-pre.prom"

set +e
python3 "$HERE/smoke.py" \
  --arm "$ARM" \
  --url "http://127.0.0.1:${HTTP_PORT}" \
  --metrics-url "http://127.0.0.1:${METRICS_PORT}/metrics" \
  --salt "$SALT" \
  --timeout "$REQUEST_TIMEOUT" \
  --cache-wait-seconds "$CACHE_WAIT_SECONDS" \
  --background-requests "$BACKGROUND_REQUESTS" \
  --pool-background-requests "$POOL_BACKGROUND_REQUESTS" \
  --direct-background-requests-per-rank \
    "$DIRECT_BACKGROUND_REQUESTS_PER_RANK" \
  --direct-prefill-url-template 'http://127.0.0.1:{port}' \
  --direct-prefill-port-base "$DIRECT_HTTP_PORT_BASE" \
  --direct-ranks "$DIRECT_RANKS" \
  --trigger-minimum-inflight-tokens "$TRIGGER_MINIMUM_INFLIGHT_TOKENS" \
  --minimum-inflight-tokens "$MINIMUM_INFLIGHT_TOKENS" \
  --minimum-loaded-ranks "$MINIMUM_LOADED_RANKS" \
  --require-capacity-queued-ranks "$REQUIRE_CAPACITY_QUEUED_RANKS" \
  --load-wait-timeout "$LOAD_WAIT_TIMEOUT" \
  --kube-context "$KUBE_CONTEXT" \
  --namespace "$NS" \
  > "$TMP_DIR/result.json"
STATUS=$?
set -e

kill "$DECISION_LOG_PID" 2>/dev/null || true
wait "$DECISION_LOG_PID" 2>/dev/null || true
DECISION_LOG_PID=

python3 "$HERE/counters.py" snapshot \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --output "$TMP_DIR/counters-post.json" || STATUS=1
python3 "$HERE/counters.py" diff \
  "$TMP_DIR/counters-pre.json" "$TMP_DIR/counters-post.json" \
  > "$TMP_DIR/counter-deltas.json" || STATUS=1
python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${METRICS_PORT}/metrics', timeout=10).read().decode(), end='')" \
  > "$TMP_DIR/epp-metrics-post.prom" || STATUS=1
k -n "$NS" logs "deploy/${EPP}" -c epp --since-time "$START_TIME" \
  > "$TMP_DIR/epp.log" 2>&1 || true

k -n "$NS" cp "$TMP_DIR/." "$PVC_ACCESS_POD:$REMOTE_DIR"
cat "$TMP_DIR/result.json"
echo "artifacts: ${NS}/${PVC_ACCESS_POD}:${REMOTE_DIR}"
exit "$STATUS"
