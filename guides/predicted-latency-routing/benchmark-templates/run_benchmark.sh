#!/bin/bash
set -euo pipefail

# Model + tokenizer
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"

# Use IP from environment (exported in the 'export IP' step)
if [ -z "${IP:-}" ]; then
  echo "ERROR: IP environment variable is not set. Please export IP=... before running."
  exit 1
fi
BASE_URL="http://${IP}:80"

# Use Namespace from environment
if [ -z "${NAMESPACE:-}" ]; then
  echo "ERROR: NAMESPACE environment variable is not set. Please export NAMESPACE=... before running."
  exit 1
fi

CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-10 20 30 40 50 60 70 80 90 100}"
TURNS_PER_CONV="${TURNS_PER_CONV:-6}"
WORKLOAD="${WORKLOAD:-code-generation}"

if ! command -v envsubst >/dev/null 2>&1; then
  echo "ERROR: envsubst not found. Install gettext (brew install gettext / apt install gettext)." >&2
  exit 1
fi

# --- Best-effort cleanup of cluster resources created by this run ---
PVC_NAME=""
COPY_POD=""
CONFIG_FILE=""
TRACKED_JOBS=()
TRACKED_CONFIGMAPS=()

cleanup() {
  local code=$?
  set +eu
  trap - EXIT INT TERM
  echo "Cleaning up benchmark resources..."
  [ -n "$CONFIG_FILE" ] && rm -f "$CONFIG_FILE" >/dev/null 2>&1
  for j in "${TRACKED_JOBS[@]}"; do
    kubectl delete job "$j" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1
  done
  for cm in "${TRACKED_CONFIGMAPS[@]}"; do
    kubectl delete configmap "$cm" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1
  done
  [ -n "$COPY_POD" ] && kubectl delete pod "$COPY_POD" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1
  [ -n "$PVC_NAME" ] && kubectl delete pvc "$PVC_NAME" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1
  exit $code
}
trap cleanup EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RUN_NAME="$WORKLOAD"
WORKLOAD_YAML="workloads/${WORKLOAD}/${WORKLOAD}.yaml"

if [ ! -f "$WORKLOAD_YAML" ]; then
  echo "ERROR: workload config not found: $WORKLOAD_YAML" >&2
  exit 1
fi

EPP_DEPLOYMENT="${EPP_DEPLOYMENT:-predicted-latency-routing-epp}"

export MODEL_NAME
export BASE_URL

echo "################################################################"
echo "SCENARIO: $RUN_NAME  target=$BASE_URL  namespace=$NAMESPACE"
echo "################################################################"

# --- PHASE 0: RESTART EPP ---
echo "Restarting EPP..."
kubectl rollout restart deployment/${EPP_DEPLOYMENT} -n "${NAMESPACE}"
kubectl rollout status deployment/${EPP_DEPLOYMENT} -n "${NAMESPACE}" --timeout=120s
echo "EPP ready."

# --- PHASE 1: PREPARE OUTPUT DIRECTORY + PVC ---
RUN_ID="$(date +%s)"
OUTPUT_DIR="workloads/${WORKLOAD}/results/${RUN_ID}"
mkdir -p "$OUTPUT_DIR"
echo "Results will be saved to: $OUTPUT_DIR"

PVC_NAME="inference-perf-results-${RUN_ID}"
export PVC_NAME
kubectl apply -n "${NAMESPACE}" -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${PVC_NAME}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
EOF
echo "Created PVC: $PVC_NAME"

# --- PHASE 2: SUBMIT JOBS (one per concurrency level) ---
echo "Submitting inference-perf Jobs across c-levels: $CONCURRENCY_LEVELS"
for conc in $CONCURRENCY_LEVELS; do
  export CONC="$conc"
  export SEED="$conc"
  export NUM_CONVERSATIONS="$conc"
  export MAX_REQUESTS=$((NUM_CONVERSATIONS * TURNS_PER_CONV))
  export SUFFIX="$(date +%s)${conc}"
  export BASE_URL
  export REPORT_PREFIX="c${conc}"

  job_name="inference-perf-${SUFFIX}"
  echo "    → c=$conc  num_conversations=$conc  max_requests=$MAX_REQUESTS  job=$job_name"

  CONFIG_FILE="$(mktemp "${TMPDIR:-/tmp}/inference-perf-config-${SUFFIX}.XXXXXX")"
  envsubst < "$WORKLOAD_YAML" > "$CONFIG_FILE"

  kubectl create configmap "inference-perf-config-${SUFFIX}" -n "${NAMESPACE}" \
      --from-file=config.yml="$CONFIG_FILE" \
      --dry-run=client -o yaml | kubectl apply -f -
  TRACKED_CONFIGMAPS+=("inference-perf-config-${SUFFIX}")

  envsubst < bench-job.yaml | kubectl apply -f - -n "${NAMESPACE}"
  TRACKED_JOBS+=("$job_name")
  rm -f "$CONFIG_FILE"

  echo "      Waiting for benchmark to complete..."
  kubectl wait --for=condition=complete job/"$job_name" -n "${NAMESPACE}" --timeout=14400s &
  wait_pid=$!
  while kill -0 $wait_pid 2>/dev/null; do
    if kubectl get job "$job_name" -n "${NAMESPACE}" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null | grep -q True; then
      kill $wait_pid 2>/dev/null || true
      echo "      WARN: Job $job_name failed"
      break
    fi
    sleep 10
  done
  wait $wait_pid 2>/dev/null || true

  # --- PHASE 3: CLEANUP JOB ---
  echo "      Cleaning up $job_name..."
  kubectl delete job "$job_name" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true
  kubectl delete configmap "inference-perf-config-${SUFFIX}" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true
done

echo "Downloading results from PVC $PVC_NAME..."

# --- PHASE 4: DOWNLOAD ALL RESULTS FROM PVC ---
COPY_POD="inference-perf-copy-${RUN_ID}"
kubectl apply -n "${NAMESPACE}" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${COPY_POD}
spec:
  restartPolicy: Never
  containers:
    - name: copy
      image: busybox
      command: ["sleep", "3600"]
      volumeMounts:
        - name: results
          mountPath: /tmp/reports
  volumes:
    - name: results
      persistentVolumeClaim:
        claimName: ${PVC_NAME}
EOF
kubectl wait pod/"${COPY_POD}" -n "${NAMESPACE}" --for=condition=ready --timeout=120s
kubectl cp -n "${NAMESPACE}" "${COPY_POD}:/tmp/reports/." "./$OUTPUT_DIR/"
kubectl delete pod "${COPY_POD}" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true
kubectl delete pvc "${PVC_NAME}" -n "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true

echo "Finished $RUN_NAME. Results are in $OUTPUT_DIR"
