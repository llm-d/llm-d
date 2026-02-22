#!/bin/bash

# ==============================================================================
# Configuration & Setup
# ==============================================================================
NAMESPACE="${NAMESPACE:-llm-d-nebius}"
GATEWAY_NAME="${GATEWAY_NAME:-llm-d-inference-gateway}"
BENCHMARK_DIR="${BENCHMARK_DIR:-./bench-random}"
OUTPUT_DIR="${BENCHMARK_DIR}-results"
# RAW_IP="${RAW_IP:-10.145.217.87}"
# RAW_PORT="${RAW_PORT:-80}"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "Starting benchmark in namespace: ${GREEN}${NAMESPACE}${NC}"

# Check for required tools
for tool in kubectl; do
    if ! command -v $tool &> /dev/null; then
        echo -e "${RED}Error: $tool is not installed.${NC}"
        exit 1
    fi
done

# Check for required files
if [ ! -d "$BENCHMARK_DIR" ]; then
     echo -e "${RED}Error: Directory '$BENCHMARK_DIR' not found. Are you in the correct directory?${NC}"
     exit 1
fi

# ==============================================================================
# Step 1: Verify Gateway
# ==============================================================================
echo -e "\n--- Checking Gateway ---"

if [ -z "$RAW_IP" ]; then
  RAW_IP=$(kubectl get gateway "$GATEWAY_NAME" -n "${NAMESPACE}" -o jsonpath='{.status.addresses[0].value}' 2>/dev/null)
  RAW_PORT=80
fi

if [ -z "$RAW_IP" ]; then
    echo -e "${RED}Gateway IP not assigned yet. Cannot run benchmark.${NC}"
    exit 1
else
    echo -e "Gateway IP found: ${GREEN}${RAW_IP}${NC}"
    BASE_URL="http://${RAW_IP}:${RAW_PORT}"
    echo "Target URL: $BASE_URL"

    # Update the config.yml with the dynamic Gateway IP
    sed "s|base_url: .*|base_url: ${BASE_URL}|" "${BENCHMARK_DIR}/config.yml" > "${BENCHMARK_DIR}/config.yml.tmp" && mv "${BENCHMARK_DIR}/config.yml.tmp" "${BENCHMARK_DIR}/config.yml"
    echo "Updated base_url in ${BENCHMARK_DIR}/config.yml"
fi

# ==============================================================================
# Step 2: Prepare Benchmark Configuration
# ==============================================================================
echo -e "\n--- Preparing Benchmark Configuration ---"

# Update ConfigMap
echo "Updating ConfigMap 'inference-perf-config'..."
kubectl delete configmap inference-perf-config -n "${NAMESPACE}" --ignore-not-found=true
kubectl create configmap inference-perf-config -n "${NAMESPACE}" --from-file="${BENCHMARK_DIR}/config.yml"

# ==============================================================================
# Step 3: Run Benchmark Job
# ==============================================================================
echo -e "\n--- Starting Benchmark Job ---"

# Clean up previous job
if kubectl get job inference-perf -n "${NAMESPACE}" > /dev/null 2>&1; then
    echo "Deleting previous benchmark job..."
    kubectl delete job inference-perf -n "${NAMESPACE}" --wait=true
fi

echo "Deploying benchmark job..."
kubectl apply -f benchmark.yaml -n "${NAMESPACE}"

# ==============================================================================
# Step 4: Wait for Execution
# ==============================================================================
echo -e "\n--- Waiting for Benchmark Execution ---"

echo "Waiting for pod to be ready..."
if kubectl wait --for=condition=Ready pod -l app=inference-perf -n "${NAMESPACE}" --timeout=1200s > /dev/null; then
    POD=$(kubectl get pods -l app=inference-perf -n "${NAMESPACE}" -o jsonpath='{.items[0].metadata.name}')
    echo -e "Benchmark Pod running: ${GREEN}${POD}${NC}"
else
    echo -e "${RED}Timeout waiting for benchmark pod to be ready.${NC}"
    exit 1
fi

echo "Waiting for benchmark completion (this may take a while)..."
until kubectl logs "$POD" -n "${NAMESPACE}" 2>/dev/null | grep -q "Benchmark finished"; do
    # Check if pod failed
    PHASE=$(kubectl get pod "$POD" -n "${NAMESPACE}" -o jsonpath='{.status.phase}')
    if [ "$PHASE" == "Failed" ]; then
        echo -e "\n${RED}Benchmark Pod Failed.${NC}"
        exit 1
    fi
    echo -n "."
    sleep 5
done
echo ""

echo -e "Benchmark status: ${GREEN}FINISHED${NC}"

# ==============================================================================
# Step 5: Retrieve Results
# ==============================================================================
echo -e "\n--- Retrieving Results ---"

# Get the report directory path from logs
REPORT_DIR=$(kubectl logs "$POD" -n "${NAMESPACE}" | grep "Report files will be stored at" | awk -F': ' '{print $NF}' | tr -d '\r')

if [ -z "$REPORT_DIR" ]; then
    echo -e "${RED}Could not determine remote report directory from logs.${NC}"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
echo "Copying report from ${POD}:${REPORT_DIR} to ${OUTPUT_DIR}..."

kubectl exec "${POD}" -n "${NAMESPACE}" -- /bin/sh -c "cat ${REPORT_DIR}/summary_lifecycle_metrics.json"

if kubectl cp "${NAMESPACE}/${POD}:${REPORT_DIR}" "$OUTPUT_DIR"; then
    echo -e "${GREEN}Results successfully copied to ${OUTPUT_DIR}${NC}"
else
    echo -e "${RED}Failed to copy results.${NC}"
    exit 1
fi

echo -e "\n${GREEN}Benchmark completed successfully!${NC}"