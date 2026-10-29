#!/bin/bash
# calibrate-decode.sh — measure peak decode throughput (V_D) against a live
# decode pool and compute P:D disaggregation ratios.
#
# This script measures the maximum output token rate a decode replica sustains
# while satisfying a p90 TPOT (time per output token) latency target:
#
#   P:D = (ISL / V_P) : (OSL / V_D)
#
# Usage:
#   GUIDE_NAME=optimized-baseline NAMESPACE=default \
#   MODEL_NAME=Qwen/Qwen3-32B ISL=64 OSL=512 TARGET_TPOT_MS=50 \
#   PEAK_PREFILL_THROUGHPUT=15928 ./calibrate-decode.sh
#
# Required environment:
#   GUIDE_NAME   — release/guide name; used for the <name>-epp service (default: optimized-baseline)
#   NAMESPACE    — the K8s namespace the stack runs in (default: default)
#
# Optional environment (auto-discovered or defaulted if not set):
#   VLLM_ENDPOINT           — http://host:port; defaults to the EPP service ClusterIP
#   MODEL_NAME              — model name vLLM is serving (default: Qwen/Qwen3-32B)
#   DECODE_REPLICAS         — number of decode replicas in the pool (default: 1)
#   ISL                     — input sequence length in tokens (default: 64)
#   OSL                     — output sequence length in tokens (default: 512)
#   TARGET_TPOT_MS          — p90 TPOT target in milliseconds (default: 50.0)
#   CONCURRENCY_LADDER      — comma-separated concurrency levels (default: 1,2,4,8,16,32)
#   REQUESTS_PER_WORKER     — requests per concurrency thread (default: 2)
#   NUM_WARMUP              — warmup requests (default: 2)
#   PEAK_PREFILL_THROUGHPUT — V_P from calibrate.sh; used to compute P:D ratio (optional)
#
# Prerequisites:
#   - Model server or router is running and reachable from the Job's network
#   - kubectl and envsubst available in PATH

set -euo pipefail

GUIDE_NAME="${GUIDE_NAME:-optimized-baseline}"
NAMESPACE="${NAMESPACE:-default}"

# Locate this script's own directory so the Job template is found regardless of working directory
CAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_TEMPLATE="${CAL_DIR}/calibration-peak-decode.yaml"
RENDERED_JOB="/tmp/${GUIDE_NAME}-calibrate-peak-decode.yaml"

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
export DECODE_REPLICAS="${DECODE_REPLICAS:-1}"
export ISL="${ISL:-64}"
export OSL="${OSL:-512}"
export TARGET_TPOT_MS="${TARGET_TPOT_MS:-50.0}"
export CONCURRENCY_LADDER="${CONCURRENCY_LADDER:-1,2,4,8,16,32}"
export REQUESTS_PER_WORKER="${REQUESTS_PER_WORKER:-2}"
export NUM_WARMUP="${NUM_WARMUP:-2}"
export PEAK_PREFILL_THROUGHPUT="${PEAK_PREFILL_THROUGHPUT:-}"

echo ""
echo "Decode Calibration inputs:"
echo "  VLLM_ENDPOINT           = $VLLM_ENDPOINT"
echo "  MODEL_NAME              = $MODEL_NAME"
echo "  DECODE_REPLICAS         = $DECODE_REPLICAS"
echo "  ISL                     = $ISL tokens"
echo "  OSL                     = $OSL tokens"
echo "  TARGET_TPOT_MS          = $TARGET_TPOT_MS ms"
echo "  CONCURRENCY_LADDER      = $CONCURRENCY_LADDER"
echo "  REQUESTS_PER_WORKER     = $REQUESTS_PER_WORKER"
echo "  NUM_WARMUP              = $NUM_WARMUP"
if [[ -n "$PEAK_PREFILL_THROUGHPUT" ]]; then
  echo "  PEAK_PREFILL_THROUGHPUT = $PEAK_PREFILL_THROUGHPUT tok/s"
fi
echo ""

# 3. Render the Job manifest with env vars substituted in
envsubst < "$JOB_TEMPLATE" > "$RENDERED_JOB"

# 4. Clear any old Job from a previous run
kubectl delete job calibrate-peak-decode -n "$NAMESPACE" --ignore-not-found

# 5. Apply the rendered Job
echo "Running decode calibration Job..."
kubectl apply -f "$RENDERED_JOB" -n "$NAMESPACE"

# 6. Wait for completion
echo "Waiting for Job to complete (up to 10 minutes)..."
kubectl wait --for=condition=complete --timeout=600s -n "$NAMESPACE" job/calibrate-peak-decode \
  || {
    echo "ERROR: decode calibration Job did not complete successfully"
    echo "--- Job logs ---"
    kubectl logs -n "$NAMESPACE" job/calibrate-peak-decode || true
    exit 1
  }

# 7. Extract results from Job output
JOB_LOGS=$(kubectl logs -n "$NAMESPACE" job/calibrate-peak-decode)
PEAK_DECODE_THROUGHPUT=$(echo "$JOB_LOGS" | grep '^PEAK_DECODE_THROUGHPUT=' | tail -1 | cut -d= -f2)
MEASURED_P90_TPOT_MS=$(echo "$JOB_LOGS" | grep '^MEASURED_P90_TPOT_MS=' | tail -1 | cut -d= -f2)
SATISFIED_CONCURRENCY=$(echo "$JOB_LOGS" | grep '^SATISFIED_CONCURRENCY=' | tail -1 | cut -d= -f2)
RECOMMENDED_P_D_RATIO=$(echo "$JOB_LOGS" | grep '^RECOMMENDED_P_D_RATIO=' | tail -1 | cut -d= -f2 || true)

if [[ -z "$PEAK_DECODE_THROUGHPUT" ]]; then
  echo "ERROR: Job completed but didn't emit a PEAK_DECODE_THROUGHPUT= line"
  echo "$JOB_LOGS"
  exit 1
fi

# 8. Report the measured value and sizing recommendations
echo ""
echo "========================================================================"
echo "  Decode Calibration Complete."
echo ""
echo "  Measured peakDecodeThroughput (V_D) = $PEAK_DECODE_THROUGHPUT tokens/sec"
echo "  Optimal concurrency per pod        = $SATISFIED_CONCURRENCY"
echo "  Achieved p90 TPOT                  = $MEASURED_P90_TPOT_MS ms (SLO: <= $TARGET_TPOT_MS ms)"
echo ""
if [[ -n "$RECOMMENDED_P_D_RATIO" ]]; then
  echo "  Calculated Sizing Ratio for ISL=$ISL / OSL=$OSL:"
  echo "    Recommended P:D Replica Ratio = $RECOMMENDED_P_D_RATIO"
  echo ""
  echo "  Next steps:"
  echo "    Apply this ratio to your DisaggregatedSet or guide manifests:"
  echo "    For example, with ratio $RECOMMENDED_P_D_RATIO, configure:"
  echo "      prefill.replicas = $(echo "$RECOMMENDED_P_D_RATIO" | cut -d: -f1)"
  echo "      decode.replicas  = $(echo "$RECOMMENDED_P_D_RATIO" | cut -d: -f2)"
else
  echo "  Next steps:"
  echo "    To compute the exact P:D replica ratio for your workload, run with"
  echo "    your prefill throughput (V_P):"
  echo "      PEAK_PREFILL_THROUGHPUT=<value> ./calibrate-decode.sh"
fi
echo "========================================================================"
