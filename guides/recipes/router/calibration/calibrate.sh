#!/bin/bash
# calibrate.sh — measure peak prefill throughput against a live llm-d router
# deployment. Reusable across any guide whose router config sets
# `peakPrefillThroughput` on the prefix-cache-affinity-filter plugin.
#
# This script ONLY measures and prints the value — it does not modify any
# config. Copy the printed number into your guide's router values file
# (prefix-cache-affinity-filter.parameters.peakPrefillThroughput), then
# helm upgrade the router release and restart the EPP.
#
# Usage:
#   GUIDE_NAME=agentic-serving NAMESPACE=llm-d-agentic-serving \
#   MODEL_NAME=Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 CHUNK_SIZE=8192 ./calibrate.sh
#
# Required environment:
#   GUIDE_NAME   — release/guide name; used for the <name>-epp service (default: optimized-baseline)
#   NAMESPACE    — the K8s namespace the stack runs in (default: default)
#
# Optional environment (auto-discovered or defaulted if not set):
#   VLLM_ENDPOINT     — http://host:port; defaults to the EPP service ClusterIP
#   MODEL_NAME        — model name vLLM is serving (default: Qwen/Qwen3-32B)
#   CHUNK_SIZE        — must match vLLM --max-num-batched-tokens (default: 8192)
#   T_MAX_SECONDS     — TTFT SLO tolerance; informational TAU line only (default: 18)
#   NUM_WARMUP        — warmup requests (default: 5)
#   NUM_MEASUREMENTS  — measurement requests (default: 20)
#
# Prerequisites:
#   - vLLM is running and reachable from the calibrate Job's network
#   - kubectl and envsubst available in PATH

set -euo pipefail

GUIDE_NAME="${GUIDE_NAME:-optimized-baseline}"
NAMESPACE="${NAMESPACE:-default}"

# Locate this script's own directory so the Job template is found regardless of
# the working directory.
CAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_TEMPLATE="${CAL_DIR}/calibration-peak-throughput.yaml"
RENDERED_JOB="/tmp/${GUIDE_NAME}-calibrate-peak-throughput.yaml"

# 1. Pre-flight checks
command -v envsubst >/dev/null \
  || { echo "ERROR: envsubst not installed (try: apt-get install gettext-base)"; exit 1; }
[[ -f "$JOB_TEMPLATE" ]] || { echo "ERROR: Job template not found at $JOB_TEMPLATE"; exit 1; }

# 2. Auto-discover the EPP ClusterIP if VLLM_ENDPOINT isn't explicitly set
if [[ -z "${VLLM_ENDPOINT:-}" ]]; then
  EPP_SVC="${GUIDE_NAME}-epp"
  EPP_IP=$(kubectl get service "$EPP_SVC" -n "$NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  if [[ -z "$EPP_IP" ]]; then
    echo "ERROR: couldn't auto-discover EPP ClusterIP. Set VLLM_ENDPOINT manually."
    exit 1
  fi
  EPP_PORT=$(kubectl get service "$EPP_SVC" -n "$NAMESPACE" -o jsonpath='{.spec.ports[?(@.name=="http")].port}' 2>/dev/null || echo "80")
  export VLLM_ENDPOINT="http://${EPP_IP}:${EPP_PORT}"
  echo "Auto-discovered VLLM_ENDPOINT=$VLLM_ENDPOINT (via EPP service)"
fi

# Apply defaults for remaining env vars
export MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
export CHUNK_SIZE="${CHUNK_SIZE:-8192}"
export T_MAX_SECONDS="${T_MAX_SECONDS:-18}"
export NUM_WARMUP="${NUM_WARMUP:-5}"
export NUM_MEASUREMENTS="${NUM_MEASUREMENTS:-20}"

echo ""
echo "Calibration inputs:"
echo "  VLLM_ENDPOINT    = $VLLM_ENDPOINT"
echo "  MODEL_NAME       = $MODEL_NAME"
echo "  CHUNK_SIZE       = $CHUNK_SIZE"
echo "  T_MAX_SECONDS    = $T_MAX_SECONDS"
echo "  NUM_WARMUP       = $NUM_WARMUP"
echo "  NUM_MEASUREMENTS = $NUM_MEASUREMENTS"
echo ""

# 3. Render the Job manifest with the env vars substituted in
envsubst < "$JOB_TEMPLATE" > "$RENDERED_JOB"

# 4. Clear any old Job from a previous run
kubectl delete job calibrate-peak-throughput -n "$NAMESPACE" --ignore-not-found

# 5. Apply the rendered Job
echo "Running calibration Job..."
kubectl apply -f "$RENDERED_JOB" -n "$NAMESPACE"

# 6. Wait for completion
echo "Waiting for Job to complete (up to 5 minutes)..."
kubectl wait --for=condition=complete --timeout=300s -n "$NAMESPACE" job/calibrate-peak-throughput \
  || {
    echo "ERROR: calibration Job did not complete successfully"
    echo "--- Job logs ---"
    kubectl logs -n "$NAMESPACE" job/calibrate-peak-throughput || true
    exit 1
  }

# 7. Extract the measured peak prefill throughput from the Job's stdout
PEAK_PREFILL_THROUGHPUT=$(kubectl logs -n "$NAMESPACE" job/calibrate-peak-throughput | grep '^PEAK_PREFILL_THROUGHPUT=' | tail -1 | cut -d= -f2)
if [[ -z "$PEAK_PREFILL_THROUGHPUT" ]]; then
  echo "ERROR: Job completed but didn't emit a PEAK_PREFILL_THROUGHPUT= line"
  kubectl logs -n "$NAMESPACE" job/calibrate-peak-throughput
  exit 1
fi

# 8. Report the measured value and how to apply it (no auto-apply)
echo ""
echo "========================================================================"
echo "  Calibration complete."
echo ""
echo "  Measured peakPrefillThroughput = $PEAK_PREFILL_THROUGHPUT tokens/sec"
echo ""
echo "  Next steps:"
echo "    1. Set this value on the prefix-cache-affinity-filter plugin in your"
echo "       guide's router values file:"
echo ""
echo "         - type: prefix-cache-affinity-filter"
echo "           parameters:"
echo "             peakPrefillThroughput: $PEAK_PREFILL_THROUGHPUT"
echo ""
echo "    2. Re-apply the router release (helm upgrade ... -f <your-guide>.values.yaml)"
echo "       and restart the EPP:"
echo ""
echo "         kubectl rollout restart -n ${NAMESPACE} deployment/${GUIDE_NAME}-epp"
echo "========================================================================"
