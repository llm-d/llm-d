#!/bin/bash
# calibrate-min-cached-token-delta.sh — measure the pull-versus-recompute
# crossover between two live model-server pods and print the recommended
# `minCachedTokenDelta` for the p2p-source-producer.
#
# This script ONLY measures and prints the value — it does not modify any
# config. Copy the printed number into your router values
# (p2p-source-producer.parameters.minCachedTokenDelta), then helm upgrade
# the router release and restart the EPP.
#
# Unlike the peak-throughput calibration, this cannot go through the router:
# the pull is driven by injecting kv_transfer_params directly at two engine
# endpoints, so the Job talks to two pod IPs. The pods are auto-discovered
# from POD_SELECTOR; the first becomes the source, the second the consumer.
#
# Usage:
#   NAMESPACE=llm-d-p2p POD_SELECTOR=llm-d.ai/guide=p2p-kv-cache-sharing \
#   MODEL_NAME=openai/gpt-oss-120b ./calibrate-min-cached-token-delta.sh
#
#   On a data-parallel engine, pass its --data-parallel-size as well:
#   ... DP_RANKS=16 ./calibrate-min-cached-token-delta.sh
#
# Required environment:
#   NAMESPACE     — the K8s namespace the stack runs in
#   POD_SELECTOR  — label selector matching the model-server pods
#   MODEL_NAME    — model name vLLM is serving
#
# Optional environment:
#   ENGINE_PORT   — the vLLM engine port on the pod (default: 8200, the port
#                   behind the routing sidecar in the p2p guide; use 8000 if
#                   the engine listens there directly)
#   P2P_PORT      — the P2P tier listener (default: 7777)
#   LENGTHS       — comma-separated prefix lengths to test; multiples of the
#                   vLLM block size (default: 2048,4096,8192,16384,32768)
#   REPS          — repetitions per length, medians reported (default: 5)
#   DP_RANKS      — engine's --data-parallel-size (default: 1). vLLM offsets
#                   the P2P listener port by GLOBAL DP rank, so P2P_PORT
#                   addresses rank 0 only. On an idle engine the balancer
#                   sends each sequential seed to the least-loaded rank, which
#                   is rank 0, so the default seeds the right listener and a
#                   DP>1 fleet calibrates correctly without this. Set it to
#                   the DP size when the engine is not idle: the seed is then
#                   issued as that many CONCURRENT identical requests so every
#                   rank holds the prefix regardless of which one the balancer
#                   picks.
#
# Prerequisites:
#   - The model servers run the OffloadingConnector with a P2P secondary tier
#     and PYTHONHASHSEED pinned fleet-wide (see the p2p guide's Best
#     Practices — without it no block hash ever matches across pods and the
#     pull silently measures zero).
#   - Run on the transport you will deploy on. The crossover is
#     transport-dependent: measured on gpt-oss-120b/H200 it sits below 2K
#     with RDMA and near 29K on the TCP fallback.

set -euo pipefail

NAMESPACE="${NAMESPACE:?set NAMESPACE}"
POD_SELECTOR="${POD_SELECTOR:?set POD_SELECTOR (label selector for model-server pods)}"
export MODEL_NAME="${MODEL_NAME:?set MODEL_NAME}"
ENGINE_PORT="${ENGINE_PORT:-8200}"
export P2P_PORT="${P2P_PORT:-7777}"
export LENGTHS="${LENGTHS:-2048,4096,8192,16384,32768}"
export REPS="${REPS:-5}"
export DP_RANKS="${DP_RANKS:-1}"

CAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_TEMPLATE="${CAL_DIR}/calibration-min-cached-token-delta.yaml"
RENDERED_JOB="/tmp/calibrate-min-cached-token-delta.yaml"

command -v envsubst >/dev/null \
  || { echo "ERROR: envsubst not installed (try: apt-get install gettext-base)"; exit 1; }
[[ -f "$JOB_TEMPLATE" ]] || { echo "ERROR: Job template not found at $JOB_TEMPLATE"; exit 1; }

# Discover two Ready model-server pods; first = source, second = consumer.
# Ready is the condition that matters - a pod can be phase=Running while its
# engine is still loading weights and serving nothing. Plain `while read`
# rather than `mapfile`, which is bash 4+ and absent from the bash 3.2 that
# ships with macOS.
PODS=""
while IFS= read -r line; do
  [[ -n "$line" ]] && PODS="${PODS}${line% True}"$'\n'
done < <(kubectl get pods -n "$NAMESPACE" -l "$POD_SELECTOR" \
  -o jsonpath='{range .items[*]}{.metadata.name} {.status.podIP} {range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' \
  | grep ' True$' | head -2)

POD_COUNT=$(printf '%s' "$PODS" | grep -c . || true)
[[ "$POD_COUNT" -ge 2 ]] || {
  echo "ERROR: need at least 2 Ready pods matching '$POD_SELECTOR' (found ${POD_COUNT})"; exit 1; }

SRC_LINE=$(printf '%s' "$PODS" | sed -n '1p')
DST_LINE=$(printf '%s' "$PODS" | sed -n '2p')
SRC_POD=${SRC_LINE%% *}; export SRC_IP=${SRC_LINE##* }
DST_POD=${DST_LINE%% *}; DST_IP=${DST_LINE##* }
export SRC_URL="http://${SRC_IP}:${ENGINE_PORT}"
export DST_URL="http://${DST_IP}:${ENGINE_PORT}"

echo "Calibration inputs:"
echo "  source pod    = ${SRC_POD} (${SRC_URL}, p2p ${SRC_IP}:${P2P_PORT})"
echo "  consumer pod  = ${DST_POD} (${DST_URL})"
echo "  MODEL_NAME    = ${MODEL_NAME}"
echo "  LENGTHS       = ${LENGTHS}"
echo "  REPS          = ${REPS}"
echo "  DP_RANKS      = ${DP_RANKS}"
echo ""

envsubst < "$JOB_TEMPLATE" > "$RENDERED_JOB"
kubectl delete job calibrate-min-cached-token-delta -n "$NAMESPACE" --ignore-not-found
echo "Running calibration Job..."
kubectl apply -f "$RENDERED_JOB" -n "$NAMESPACE"

# A 5-length x 5-rep sweep with per-probe seeding takes several minutes.
echo "Waiting for Job to complete (up to 20 minutes)..."
kubectl wait --for=condition=complete --timeout=1200s -n "$NAMESPACE" \
  job/calibrate-min-cached-token-delta || {
    echo "ERROR: calibration Job did not complete successfully"
    echo "--- Job logs ---"
    kubectl logs -n "$NAMESPACE" job/calibrate-min-cached-token-delta || true
    exit 1
  }

echo "--- Job output ---"
kubectl logs -n "$NAMESPACE" job/calibrate-min-cached-token-delta

VALUE=$(kubectl logs -n "$NAMESPACE" job/calibrate-min-cached-token-delta \
  | grep '^MIN_CACHED_TOKEN_DELTA=' | tail -1 | cut -d= -f2)
[[ -n "$VALUE" ]] || { echo "ERROR: Job completed but emitted no MIN_CACHED_TOKEN_DELTA= line"; exit 1; }

echo ""
echo "Recommended p2p-source-producer setting:"
echo ""
echo "  - type: p2p-source-producer"
echo "    parameters:"
echo "      minCachedTokenDelta: ${VALUE}"
echo ""
echo "(the smallest tested length at which the pull beat recompute; a pull is"
echo " requested only when a peer holds at least this many more cached prefix"
echo " tokens than the scheduled pod)"
