#!/bin/bash

# Simple P/D Disaggregation Test Script

set -euo pipefail

NAMESPACE="llm-d-pd"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "🧪 Testing P/D Disaggregation Deployment"
echo "========================================"
echo ""

# Test 1: Namespace
print_status "1. Checking namespace..."
if kubectl get namespace $NAMESPACE &>/dev/null; then
    print_success "Namespace exists"
else
    print_error "Namespace not found"
    exit 1
fi

# Test 2: Pods
print_status "2. Checking pods..."
kubectl get pods -n $NAMESPACE
echo ""

prefill_ready=$(kubectl get pods -n $NAMESPACE -l llm-d.ai/role=prefill --no-headers | grep -c "Running" || echo "0")
decode_ready=$(kubectl get pods -n $NAMESPACE -l llm-d.ai/role=decode --no-headers | grep -c "Running" || echo "0") 

if [[ $prefill_ready -eq 1 ]]; then
    print_success "Prefill pod: Ready"
else
    print_warning "Prefill pod: Not ready"
fi

if [[ $decode_ready -eq 1 ]]; then
    print_success "Decode pod: Ready"
else
    print_warning "Decode pod: Not ready"
fi

# Test 3: Services
print_status "3. Checking services..."
kubectl get svc -n $NAMESPACE
echo ""

# Test 4: Gateway
print_status "4. Checking gateway..."
kubectl get gateway -n $NAMESPACE 2>/dev/null || print_warning "Gateway not found"
echo ""

# Summary
echo "=========================================="
if [[ $prefill_ready -eq 1 && $decode_ready -eq 1 ]]; then
    print_success "🎉 P/D Disaggregation deployment looks healthy!"
    echo ""
    echo "💡 Test inference:"
    echo "kubectl port-forward -n $NAMESPACE svc/infra-pd-inference-gateway-istio 8080:80"
    echo ""
else
    print_warning "⚠️  Some components are not ready yet"
    echo ""
    echo "🔧 Check logs:"
    echo "kubectl logs -n $NAMESPACE -l llm-d.ai/role=prefill"
    echo "kubectl logs -n $NAMESPACE -l llm-d.ai/role=decode"
fi
echo ""