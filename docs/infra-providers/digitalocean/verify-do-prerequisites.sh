#!/bin/bash

# DigitalOcean DOKS Prerequisites Verification Script
# Validates cluster configuration for llm-d deployment

set -e

echo "🔍 Verifying DigitalOcean DOKS Prerequisites for llm-d..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Success/failure counters
CHECKS_PASSED=0
CHECKS_FAILED=0

check_requirement() {
    local name="$1"
    local command="$2"
    local expected_result="$3"

    echo -n "  Checking $name... "

    if eval "$command" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
        ((CHECKS_PASSED++))
        return 0
    else
        echo -e "${RED}✗${NC}"
        echo -e "    ${YELLOW}Expected: $expected_result${NC}"
        ((CHECKS_FAILED++))
        return 1
    fi
}

echo ""
echo "📋 Cluster Information"
echo "====================="

# Basic cluster connectivity
check_requirement "kubectl connectivity" "kubectl cluster-info" "Cluster endpoint accessible"

# DOKS-specific labels
DOKS_NODE_COUNT=$(kubectl get nodes -l doks.digitalocean.com/node-pool --no-headers 2>/dev/null | wc -l)
if [ "$DOKS_NODE_COUNT" -gt 0 ]; then
    echo -e "  DOKS cluster detected: ${GREEN}✓${NC} ($DOKS_NODE_COUNT nodes)"
    ((CHECKS_PASSED++))
else
    echo -e "  DOKS cluster detection: ${RED}✗${NC}"
    echo -e "    ${YELLOW}Expected: Nodes with doks.digitalocean.com/node-pool label${NC}"
    ((CHECKS_FAILED++))
fi

echo ""
echo "🎯 GPU Resources"
echo "==============="

# GPU nodes availability
GPU_NODE_COUNT=$(kubectl get nodes -l nvidia.com/gpu.present=true --no-headers 2>/dev/null | wc -l)
if [ "$GPU_NODE_COUNT" -ge 2 ]; then
    echo -e "  GPU nodes available: ${GREEN}✓${NC} ($GPU_NODE_COUNT nodes)"
    ((CHECKS_PASSED++))
else
    echo -e "  GPU nodes available: ${RED}✗${NC}"
    echo -e "    ${YELLOW}Expected: At least 2 GPU nodes for P/D disaggregation${NC}"
    ((CHECKS_FAILED++))
fi

# NVIDIA Device Plugin
check_requirement "NVIDIA Device Plugin" \
    "kubectl get pods -n nvidia-device-plugin-system -l name=nvidia-device-plugin-ds --field-selector=status.phase=Running" \
    "Running device plugin pods"

# GPU resource allocation
TOTAL_GPUS=$(kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}' 2>/dev/null | tr ' ' '\n' | grep -v '^$' | paste -sd+ | bc 2>/dev/null || echo "0")
if [ "$TOTAL_GPUS" -ge 2 ]; then
    echo -e "  Allocatable GPUs: ${GREEN}✓${NC} ($TOTAL_GPUS total)"
    ((CHECKS_PASSED++))
else
    echo -e "  Allocatable GPUs: ${RED}✗${NC}"
    echo -e "    ${YELLOW}Expected: At least 2 allocatable GPUs${NC}"
    ((CHECKS_FAILED++))
fi

echo ""
echo "🌐 Networking"
echo "============"

# VPC-native networking check
VPC_NATIVE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.labels.doks\.digitalocean\.com/vpc-native}' 2>/dev/null)
if [ "$VPC_NATIVE" = "true" ]; then
    echo -e "  VPC-native networking: ${GREEN}✓${NC}"
    ((CHECKS_PASSED++))
else
    echo -e "  VPC-native networking: ${RED}✗${NC}"
    echo -e "    ${YELLOW}Expected: VPC-native enabled (required for llm-d)${NC}"
    ((CHECKS_FAILED++))
fi

# LoadBalancer support
check_requirement "LoadBalancer support" \
    "kubectl get svc -A --field-selector=spec.type=LoadBalancer" \
    "LoadBalancer services can be created"

echo ""
echo "💾 Storage"
echo "========="

# Block storage class
check_requirement "do-block-storage class" \
    "kubectl get storageclass do-block-storage" \
    "DigitalOcean block storage available"

echo ""
echo "🔧 Prerequisites"
echo "==============="

# Gateway API CRDs
check_requirement "Gateway API CRDs" \
    "kubectl get crd gateways.gateway.networking.k8s.io" \
    "Gateway API CRDs installed"

# Inference Extension CRDs
check_requirement "Inference Extension CRDs" \
    "kubectl get crd inferencepools.inference.networking.x-k8s.io" \
    "Inference Extension CRDs installed"

# Istio components
check_requirement "Istio control plane" \
    "kubectl get pods -n istio-system -l app=istiod --field-selector=status.phase=Running" \
    "Istio control plane running"

echo ""
echo "📊 Summary"
echo "========="

TOTAL_CHECKS=$((CHECKS_PASSED + CHECKS_FAILED))

if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All prerequisites met! ($CHECKS_PASSED/$TOTAL_CHECKS)${NC}"
    echo ""
    echo "🚀 Your DOKS cluster is ready for llm-d deployment!"
    echo ""
    echo "Next steps:"
    echo "  1. cd guides/inference-scheduling && helmfile apply -e digitalocean"
    echo "  2. cd guides/pd-disaggregation && helmfile apply -e digitalocean"
    exit 0
else
    echo -e "${RED}❌ Some prerequisites are missing ($CHECKS_PASSED/$TOTAL_CHECKS passed)${NC}"
    echo ""
    echo "🔧 To fix missing prerequisites:"
    echo "  1. Install Gateway API CRDs: cd guides/prereq/gateway-provider && ./install-gateway-provider-dependencies.sh"
    echo "  2. Install Istio: helmfile apply -f istio.helmfile.yaml"
    echo "  3. Ensure cluster has GPU nodes with NVIDIA drivers"
    echo "  4. Verify VPC-native networking is enabled"
    exit 1
fi