#!/usr/bin/env bash
# Run one cache-isolated AgentX C64/900s arm through one EPP service.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
KUBE_CONTEXT=${KUBE_CONTEXT:-kermit_US-EAST-01A}
NS=${NS:-nilig-agentx-slo}
PVC=${PVC:-workload-pvc}
PVC_ACCESS_POD=${PVC_ACCESS_POD:-workload-access}
REMOTE_ROOT=${REMOTE_ROOT:-/workload/p2p-context-migration-glm}
ROUTER_CONFIGMAP=${ROUTER_CONFIGMAP:-glm-cache-route-config}
MODEL=${MODEL:-zai-org/GLM-5.2-FP8}
AIPERF_IMAGE=${AIPERF_IMAGE:-quay.io/rh-ee-robshaw/aiperf:agentx-v0@sha256:9bb54497579481be375e3730dd52c353dc01e8a2fc8e0840acf36a843c3122e4}
CORPUS=${CORPUS:-semianalysis_cc_traces_weka_062126}
SEED=${SEED:-20260707}
CONCURRENCY=${CONCURRENCY:-64}
WARMUP=${WARMUP:-120}
DURATION=${DURATION:-900}
MAX_CONTEXT=${MAX_CONTEXT:-120000}
RESET_ENGINES=${RESET_ENGINES:-1}
RESTART_EPP=${RESTART_EPP:-1}
IDLE_TIMEOUT=${IDLE_TIMEOUT:-300}
EXPECTED_ENGINE_PODS=${EXPECTED_ENGINE_PODS:-3}
ENGINE_ROLLOUT_TIMEOUT=${ENGINE_ROLLOUT_TIMEOUT:-30m}
DRY_RUN=${DRY_RUN:-0}
ENGINE_STATEFULSETS=(
  glm-5-2-prefill-long
  glm-5-2-prefill-short
  glm-5-2-decode
)
ENGINE_PODS=(
  glm-5-2-prefill-long-0
  glm-5-2-prefill-short-0
  glm-5-2-decode-0
)

ARM=${1:?usage: $0 baseline|candidate}
case "$ARM" in
baseline)
  EPP=${BASELINE_EPP:-glm-cache-route-epp-baseline}
  POOL=${BASELINE_POOL:-glm-cache-route-baseline}
  ;;
candidate)
  EPP=${CANDIDATE_EPP:-glm-cache-route-epp-candidate}
  POOL=${CANDIDATE_POOL:-glm-cache-route-candidate}
  ;;
*) echo "arm must be baseline or candidate" >&2; exit 2 ;;
esac

RUN_ID=${RUN_ID:-c64-$(date -u +%Y%m%d-%H%M%S)-${ARM}}
if [[ ! "$RUN_ID" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "RUN_ID must be a lowercase Kubernetes name: ${RUN_ID}" >&2
  exit 2
fi
JOB="glm-${RUN_ID}"
if (( ${#JOB} > 63 )); then
  echo "job name exceeds 63 characters: ${JOB}" >&2
  exit 2
fi

REMOTE_DIR="${REMOTE_ROOT}/${RUN_ID}"
ARTIFACT_DIR="${REMOTE_DIR}/aiperf"
URL="http://${EPP}.${NS}:80/v1"
METRICS_URL="http://${EPP}.${NS}:9090/metrics"
DEADLINE=$((DURATION + WARMUP + 1800))
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/glm-agentx.XXXXXX")

EPP_LOG_PID=

cleanup() {
  if [[ -n "$EPP_LOG_PID" ]]; then
    kill "$EPP_LOG_PID" 2>/dev/null || true
    wait "$EPP_LOG_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

k() {
  kubectl --context "$KUBE_CONTEXT" "$@"
}

reset_engine_fleet() {
  local index pod statefulset old_uid new_uid
  local old_uids=()

  for pod in "${ENGINE_PODS[@]}"; do
    old_uid=$(k -n "$NS" get "pod/${pod}" -o jsonpath='{.metadata.uid}')
    if [[ -z "$old_uid" ]]; then
      echo "engine pod has no UID: ${NS}/${pod}" >&2
      return 1
    fi
    old_uids+=("$old_uid")
  done

  for statefulset in "${ENGINE_STATEFULSETS[@]}"; do
    k -n "$NS" rollout restart "statefulset/${statefulset}" >/dev/null
  done
  for statefulset in "${ENGINE_STATEFULSETS[@]}"; do
    k -n "$NS" rollout status "statefulset/${statefulset}" \
      --timeout="$ENGINE_ROLLOUT_TIMEOUT"
  done

  : > "$TMP_DIR/engine-reset.txt"
  for index in "${!ENGINE_PODS[@]}"; do
    pod=${ENGINE_PODS[$index]}
    k -n "$NS" wait --for=condition=Ready "pod/${pod}" \
      --timeout="$ENGINE_ROLLOUT_TIMEOUT" >/dev/null
    new_uid=$(k -n "$NS" get "pod/${pod}" -o jsonpath='{.metadata.uid}')
    if [[ -z "$new_uid" || "$new_uid" == "${old_uids[$index]}" ]]; then
      echo "engine pod UID did not change: ${NS}/${pod}" >&2
      return 1
    fi
    printf '%s\t%s\t%s\n' \
      "$pod" "${old_uids[$index]}" "$new_uid" \
      >> "$TMP_DIR/engine-reset.txt"
  done
}

render_job() {
  cat <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NS}
  labels:
    app: glm-context-routing-aiperf
    arm: ${ARM}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: ${DEADLINE}
  template:
    metadata:
      labels:
        app: glm-context-routing-aiperf
        arm: ${ARM}
    spec:
      restartPolicy: Never
      containers:
      - name: aiperf
        image: ${AIPERF_IMAGE}
        command: ["/bin/sh", "-c"]
        args:
        - |
          set -eu
          test ! -e '${ARTIFACT_DIR}'
          mkdir -p '${ARTIFACT_DIR}'
          set +e
          aiperf profile \\
            --scenario 'inferencex-agentx-mvp' \\
            --url '${URL}' \\
            --model '${MODEL}' \\
            --max-context-length ${MAX_CONTEXT} \\
            --endpoint-type chat \\
            --streaming \\
            --use-server-token-count \\
            --public-dataset '${CORPUS}' \\
            --concurrency ${CONCURRENCY} \\
            --warmup-duration ${WARMUP} \\
            --benchmark-duration ${DURATION} \\
            --random-seed ${SEED} \\
            --cache-bust first-turn-prefix \\
            --server-metrics '${METRICS_URL}' \\
            --no-gpu-telemetry \\
            --output-artifact-dir '${ARTIFACT_DIR}' \\
            --ui simple
          aiperf_status=\$?
          set -e
          printf '%s\n' "\$aiperf_status" > '${ARTIFACT_DIR}/aiperf_exit_code.txt'
          validation_status=0
          python3 '${REMOTE_DIR}/validate_agentx.py' '${ARTIFACT_DIR}' \
            --expected-model '${MODEL}' \
            --expected-url '${URL}' \
            --expected-corpus '${CORPUS}' \
            --expected-seed ${SEED} \
            --expected-concurrency ${CONCURRENCY} \
            --expected-warmup ${WARMUP} \
            --expected-duration ${DURATION} \
            --expected-max-context ${MAX_CONTEXT} \
            --require-exit-code \
            --report '${ARTIFACT_DIR}/validation.json' || validation_status=\$?
          if [ "\$aiperf_status" -ne 0 ]; then
            exit "\$aiperf_status"
          fi
          exit "\$validation_status"
        env:
        - name: AIPERF_DATASET_WEKA_LIVE_ASSISTANT_RESPONSES
          value: "1"
        - name: HF_HOME
          value: /workload/.cache/huggingface
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: llm-d-hf-token
              key: HF_TOKEN
        resources:
          requests:
            cpu: "64"
            memory: 128Gi
          limits:
            cpu: "64"
            memory: 128Gi
            ephemeral-storage: 20Gi
        volumeMounts:
        - name: workload
          mountPath: /workload
      volumes:
      - name: workload
        persistentVolumeClaim:
          claimName: ${PVC}
EOF
}

render_job > "$TMP_DIR/job.yaml"
if [[ "$DRY_RUN" == 1 ]]; then
  cat "$TMP_DIR/job.yaml"
  exit 0
fi

for command in kubectl python3; do
  command -v "$command" >/dev/null || {
    echo "required command not found: $command" >&2
    exit 1
  }
done
if [[ ! "$RESET_ENGINES" =~ ^[01]$ || ! "$RESTART_EPP" =~ ^[01]$ ]]; then
  echo "RESET_ENGINES and RESTART_EPP must be 0 or 1" >&2
  exit 2
fi
if [[ ! "$ENGINE_ROLLOUT_TIMEOUT" =~ ^[1-9][0-9]*[smh]$ ]]; then
  echo "ENGINE_ROLLOUT_TIMEOUT must be a positive kubectl duration" >&2
  exit 2
fi
for value in "$CONCURRENCY" "$WARMUP" "$DURATION" "$MAX_CONTEXT" \
  "$IDLE_TIMEOUT" "$EXPECTED_ENGINE_PODS"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "benchmark numeric settings must be positive integers: ${value}" >&2
    exit 2
  fi
done

k -n "$NS" get "svc/${EPP}" >/dev/null
k -n "$NS" get "inferencepool.inference.networking.k8s.io/${POOL}" >/dev/null
k -n "$NS" get "deploy/${EPP}" >/dev/null
k -n "$NS" get "configmap/${ROUTER_CONFIGMAP}" -o json \
  > "$TMP_DIR/router-configmap.json"
k -n "$NS" wait --for=condition=Ready "pod/${PVC_ACCESS_POD}" --timeout=2m
if k -n "$NS" get "job/${JOB}" >/dev/null 2>&1; then
  echo "job already exists: ${NS}/${JOB}" >&2
  exit 1
fi
if k -n "$NS" exec "$PVC_ACCESS_POD" -- test -e "$REMOTE_DIR"; then
  echo "artifact directory already exists: ${REMOTE_DIR}" >&2
  exit 1
fi

nonterminal_jobs=$(k -n "$NS" get jobs \
  -l app=glm-context-routing-aiperf -o json | python3 -c '
import json, sys
items = json.load(sys.stdin).get("items", [])
active = []
for item in items:
    conditions = {
        condition["type"]: condition["status"]
        for condition in item.get("status", {}).get("conditions", [])
    }
    if conditions.get("Complete") != "True" and conditions.get("Failed") != "True":
        active.append(item["metadata"]["name"])
print(" ".join(sorted(active)))')
if [[ -n "$nonterminal_jobs" ]]; then
  echo "another context-routing benchmark job is active: ${nonterminal_jobs}" >&2
  exit 1
fi

python3 -c '
import json, sys
arm = sys.argv[1]
config = json.load(sys.stdin)["data"][f"{arm}.yaml"]
assert "type: session-affinity-filter" in config
assert "header: x-correlation-id" in config
assert "evictionTtlSeconds: 3600" in config
has_subtraction = "reusableTokensProducerName: p2p-cache-source" in config
assert has_subtraction == (arm == "candidate")
' "$ARM" < "$TMP_DIR/router-configmap.json"

if [[ "$RESET_ENGINES" == 1 ]]; then
  python3 "$HERE/counters.py" wait-idle \
    --context "$KUBE_CONTEXT" --namespace "$NS" \
    --timeout "$IDLE_TIMEOUT" --expected-pods "$EXPECTED_ENGINE_PODS" \
    --output "$TMP_DIR/idle-before-reset.json"
  reset_engine_fleet
fi
python3 "$HERE/counters.py" wait-idle \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --timeout "$IDLE_TIMEOUT" --expected-pods "$EXPECTED_ENGINE_PODS" \
  --output "$TMP_DIR/idle-pre.json"

if [[ "$RESTART_EPP" == 1 ]]; then
  k -n "$NS" rollout restart "deploy/${EPP}" >/dev/null
  k -n "$NS" rollout status "deploy/${EPP}" --timeout=5m
fi
k -n "$NS" get pods -l "app=${EPP}" -o wide > "$TMP_DIR/epp-pods.txt"
k -n "$NS" logs "deploy/${EPP}" -c epp --tail=4000 \
  > "$TMP_DIR/epp-startup.log" 2>&1 || true
k -n "$NS" get "${ENGINE_PODS[@]/#/pod/}" -o json \
  > "$TMP_DIR/engine-pods-pre.json"
k -n "$NS" get pods -l "app=${EPP}" -o json \
  > "$TMP_DIR/epp-pods-pre.json"

k -n "$NS" exec "$PVC_ACCESS_POD" -- mkdir -p "$REMOTE_DIR"
cp "$HERE/validate_agentx.py" "$TMP_DIR/validate_agentx.py"

python3 "$HERE/counters.py" snapshot \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --output "$TMP_DIR/counters-pre.json"
k -n "$NS" exec "$PVC_ACCESS_POD" -- python3 -c \
  'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode(), end="")' \
  "$METRICS_URL" > "$TMP_DIR/epp-metrics-pre.prom"
k -n "$NS" get "svc/${EPP}" -o yaml > "$TMP_DIR/epp-service.yaml"
k -n "$NS" get "deploy/${EPP}" -o yaml > "$TMP_DIR/epp-deployment.yaml"
k -n "$NS" get "inferencepool.inference.networking.k8s.io/${POOL}" -o yaml \
  > "$TMP_DIR/inferencepool.yaml"
k -n "$NS" get pods -l 'llm-d.ai/model=GLM-5.2-FP8' -o wide \
  > "$TMP_DIR/engine-pods.txt"

cat > "$TMP_DIR/run.json" <<EOF
{
  "schema": 1,
  "run_id": "${RUN_ID}",
  "arm": "${ARM}",
  "epp": "${EPP}",
  "pool": "${POOL}",
  "model": "${MODEL}",
  "aiperf_image": "${AIPERF_IMAGE}",
  "corpus": "${CORPUS}",
  "seed": ${SEED},
  "concurrency": ${CONCURRENCY},
  "warmup_seconds": ${WARMUP},
  "duration_seconds": ${DURATION},
  "max_context_tokens": ${MAX_CONTEXT},
  "cache_bust": "first-turn-prefix",
  "decode_session_affinity_source": "x-correlation-id",
  "engine_reset_before_arm": ${RESET_ENGINES},
  "epp_restarted_before_arm": ${RESTART_EPP}
}
EOF
k -n "$NS" cp "$TMP_DIR/." "$PVC_ACCESS_POD:$REMOTE_DIR"

START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
k -n "$NS" logs -f "deploy/${EPP}" -c epp --since-time "$START_TIME" 2>&1 \
  | grep -E --line-buffered \
    '"level":"(error|warn)"|Session affinity|TTFT load gate|narrowed to sticky' \
  > "$TMP_DIR/epp.log" &
EPP_LOG_PID=$!
k -n "$NS" apply -f "$TMP_DIR/job.yaml" >/dev/null
echo "launched ${NS}/${JOB}: ${ARM} C${CONCURRENCY}, ${WARMUP}s warmup + ${DURATION}s measured"

STATUS=0
state=running
end=$((SECONDS + DEADLINE + 60))
while (( SECONDS < end )); do
  state=$(k -n "$NS" get "job/${JOB}" -o json | python3 -c '
import json, sys
status = json.load(sys.stdin).get("status", {})
conditions = {item["type"]: item["status"] for item in status.get("conditions", [])}
if conditions.get("Complete") == "True":
    print("complete")
elif conditions.get("Failed") == "True":
    print("failed")
else:
    print("running")')
  case "$state" in
  complete) break ;;
  failed) STATUS=1; break ;;
  esac
  sleep 15
done
if [[ "$state" != complete ]]; then
  STATUS=1
  echo "job ended in state ${state}" >&2
fi

k -n "$NS" logs "job/${JOB}" > "$TMP_DIR/aiperf.log" 2>&1 || true
k -n "$NS" get "job/${JOB}" -o yaml > "$TMP_DIR/job-final.yaml" || true
if [[ -n "$EPP_LOG_PID" ]]; then
  kill "$EPP_LOG_PID" 2>/dev/null || true
  wait "$EPP_LOG_PID" 2>/dev/null || true
  EPP_LOG_PID=
fi
k -n "$NS" get "${ENGINE_PODS[@]/#/pod/}" -o json \
  > "$TMP_DIR/engine-pods-post.json" || STATUS=1
k -n "$NS" get pods -l "app=${EPP}" -o json \
  > "$TMP_DIR/epp-pods-post.json" || STATUS=1
python3 -c '
import json, pathlib, sys

def pods(path):
    data = json.loads(pathlib.Path(path).read_text())
    return {item["metadata"]["name"]: item for item in data.get("items", [])}

def restarts(item):
    return {
        status["name"]: status.get("restartCount", 0)
        for field in ("initContainerStatuses", "containerStatuses")
        for status in item.get("status", {}).get(field, [])
    }

errors = []
for label, before_path, after_path in (
    ("engine", sys.argv[1], sys.argv[2]),
    ("epp", sys.argv[3], sys.argv[4]),
):
    before = pods(before_path)
    after = pods(after_path)
    if set(before) != set(after):
        errors.append(f"{label} pod set changed: {sorted(before)} -> {sorted(after)}")
        continue
    for name in sorted(before):
        old = before[name]
        new = after[name]
        if old["metadata"]["uid"] != new["metadata"]["uid"]:
            errors.append(f"{label} pod UID changed during arm: {name}")
        if restarts(old) != restarts(new):
            errors.append(f"{label} container restart count changed: {name}")
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in new.get("status", {}).get("conditions", [])
        )
        if not ready or new["metadata"].get("deletionTimestamp") is not None:
            errors.append(f"{label} pod is not stably Ready: {name}")
print(json.dumps({"schema": 1, "valid": not errors, "errors": errors}, indent=2))
raise SystemExit(bool(errors))
' \
  "$TMP_DIR/engine-pods-pre.json" "$TMP_DIR/engine-pods-post.json" \
  "$TMP_DIR/epp-pods-pre.json" "$TMP_DIR/epp-pods-post.json" \
  > "$TMP_DIR/runtime-validation.json" || STATUS=1
session_bindings=$(grep -c 'Session affinity - bound session to pod' \
  "$TMP_DIR/epp.log" || true)
session_migrations=$(grep -c 'Session affinity - session migrated pod' \
  "$TMP_DIR/epp.log" || true)
printf 'bindings=%s\nmigrations=%s\n' \
  "$session_bindings" "$session_migrations" \
  > "$TMP_DIR/session-affinity.txt"
if [[ "$session_bindings" -eq 0 ]]; then
  echo "decode session affinity did not bind any x-correlation-id sessions" >&2
  STATUS=1
fi
if [[ "$session_migrations" -ne 0 ]]; then
  echo "decode session affinity unexpectedly migrated ${session_migrations} sessions" >&2
  STATUS=1
fi
python3 "$HERE/counters.py" wait-idle \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --timeout "$IDLE_TIMEOUT" --expected-pods "$EXPECTED_ENGINE_PODS" \
  --output "$TMP_DIR/idle-post.json" || STATUS=1
python3 "$HERE/counters.py" snapshot \
  --context "$KUBE_CONTEXT" --namespace "$NS" \
  --output "$TMP_DIR/counters-post.json" || STATUS=1
python3 "$HERE/counters.py" diff \
  "$TMP_DIR/counters-pre.json" "$TMP_DIR/counters-post.json" \
  > "$TMP_DIR/counter-deltas.json" || STATUS=1
k -n "$NS" exec "$PVC_ACCESS_POD" -- python3 -c \
  'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode(), end="")' \
  "$METRICS_URL" > "$TMP_DIR/epp-metrics-post.prom" || STATUS=1
k -n "$NS" cp "$TMP_DIR/." "$PVC_ACCESS_POD:$REMOTE_DIR"

if ! k -n "$NS" exec "$PVC_ACCESS_POD" -- \
  test -s "$ARTIFACT_DIR/profile_export.jsonl" -a \
       -s "$ARTIFACT_DIR/profile_export_aiperf.json" -a \
       -s "$ARTIFACT_DIR/aiperf_exit_code.txt" -a \
       -s "$ARTIFACT_DIR/validation.json"; then
  echo "required aiperf artifacts are missing" >&2
  STATUS=1
fi
if ! k -n "$NS" exec "$PVC_ACCESS_POD" -- python3 -c '
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
if report.get("valid") is not True or report.get("aiperf_exit_code") != 0:
    raise SystemExit(json.dumps(report.get("errors", [])))
' "$ARTIFACT_DIR/validation.json"; then
  echo "aiperf artifacts failed validity checks" >&2
  STATUS=1
fi

echo "artifacts: ${NS}/${PVC_ACCESS_POD}:${REMOTE_DIR}"
exit "$STATUS"
