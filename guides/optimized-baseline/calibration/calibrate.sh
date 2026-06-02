#!/bin/bash
# calibrate.sh — measures tau against the live deployment and produces a
# Helm values overlay file with the suggested value.
#
# This script DOES NOT apply the value. It produces an overlay file and prints
# the exact `helm upgrade` command the operator can run if they want to apply.
# The operator inspects the rendered file, decides whether to use the suggested
# value, and runs the upgrade themselves.
#
# Usage:
#   GUIDE_NAME=optimized-baseline NAMESPACE=mynamespace ./calibrate.sh
#
# Required environment:
#   GUIDE_NAME             — the guide name (default: optimized-baseline)
#   NAMESPACE              — the K8s namespace (default: default)
#   ROUTER_CHART_VERSION   — the chart version (default: v0)
#
# Optional environment (auto-discovered or defaulted if not set):
#   VLLM_ENDPOINT     — http://host:port; defaults to EPP service ClusterIP
#   MODEL_NAME        — model name vLLM is serving (default: Qwen/Qwen3-32B)
#   CHUNK_SIZE        — must match vLLM --max-num-batched-tokens (default: 8192)
#   T_MAX_SECONDS     — SLO tolerance (default: 17)
#   NUM_WARMUP        — warmup requests (default: 5)
#   NUM_MEASUREMENTS  — measurement requests (default: 10)
#   OUTPUT_FILE       — where to write the rendered values overlay
#                       (default: /tmp/<guide>-calibration.values.yaml)
#
# Prerequisites:
#   - vLLM is running and reachable from the calibrate Job's network
#   - kubectl and envsubst available in PATH

set -euo pipefail

GUIDE_NAME="${GUIDE_NAME:-optimized-baseline}"
NAMESPACE="${NAMESPACE:-default}"
ROUTER_CHART_VERSION="${ROUTER_CHART_VERSION:-v0}"

GUIDE_DIR="guides/${GUIDE_NAME}"
CAL_DIR="${GUIDE_DIR}/calibration"
JOB_TEMPLATE="${CAL_DIR}/calibrate-tau.yaml"
VALUES_TEMPLATE="${CAL_DIR}/calibration.values.template.yaml"
RENDERED_JOB="/tmp/${GUIDE_NAME}-calibrate-tau.yaml"
OUTPUT_FILE="${OUTPUT_FILE:-${CAL_DIR}/calibration.values.yaml}"

# 1. Pre-flight checks
command -v envsubst >/dev/null \
  || { echo "ERROR: envsubst not installed (try: apt-get install gettext-base)"; exit 1; }
[[ -f "$JOB_TEMPLATE" ]] || { echo "ERROR: Job template not found at $JOB_TEMPLATE"; exit 1; }
[[ -f "$VALUES_TEMPLATE" ]] || { echo "ERROR: values template not found at $VALUES_TEMPLATE"; exit 1; }

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
kubectl delete job calibrate-tau -n "$NAMESPACE" --ignore-not-found

# 5. Apply the rendered Job
echo "Running calibration Job..."
kubectl apply -f "$RENDERED_JOB" -n "$NAMESPACE"

# 6. Wait for completion
echo "Waiting for Job to complete (up to 5 minutes)..."
kubectl wait --for=condition=complete --timeout=300s -n "$NAMESPACE" job/calibrate-tau \
  || {
    echo "ERROR: calibration Job did not complete successfully"
    echo "--- Job logs ---"
    kubectl logs -n "$NAMESPACE" job/calibrate-tau || true
    exit 1
  }

# 7. Extract TAU from the Job's stdout
TAU=$(kubectl logs -n "$NAMESPACE" job/calibrate-tau | grep '^TAU=' | tail -1 | cut -d= -f2)
if [[ -z "$TAU" ]]; then
  echo "ERROR: Job completed but didn't emit a TAU= line"
  kubectl logs -n "$NAMESPACE" job/calibrate-tau
  exit 1
fi

# 8. Render the values template with TAU substituted
TAU="$TAU" envsubst < "$VALUES_TEMPLATE" > "$OUTPUT_FILE"

# 9. Report and print suggested next step (no auto-apply)
echo ""
echo "========================================================================"
echo "  Calibration complete."
echo ""
echo "  Suggested maxTokensInFlightPenalty (tau) = $TAU"
echo ""
echo "  Rendered values overlay written to:"
echo "    $OUTPUT_FILE"
echo ""
echo "  Review the file. If you want to apply this suggested value, run:"
echo ""
echo "    helm upgrade ${GUIDE_NAME} \\"
echo "      oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \\"
echo "      --version ${ROUTER_CHART_VERSION} \\"
echo "      -f guides/recipes/router/base.values.yaml \\"
echo "      -f ${GUIDE_DIR}/router/${GUIDE_NAME}.values.yaml \\"
echo "      -f $OUTPUT_FILE \\"
echo "      -n ${NAMESPACE}"
echo ""
echo "    kubectl rollout restart -n ${NAMESPACE} deployment/${GUIDE_NAME}-epp"
echo "========================================================================"