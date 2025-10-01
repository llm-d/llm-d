#!/bin/bash

# DigitalOcean-Specific Prerequisites Setup
# Only handles DigitalOcean-specific configurations before standard Helm deployment

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${BLUE}[DO-SPECIFIC]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[DO-SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[DO-WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[DO-ERROR]${NC} $1"
}

show_usage() {
    cat << EOF
🔧 DigitalOcean-Specific Prerequisites Setup

This script ONLY handles DigitalOcean-specific configurations:
- GPU node validation and tolerations
- DOKS networking setup
- DigitalOcean storage class configuration
- GPU driver validation

📖 Official Documentation:
    DOKS GPU Setup: https://docs.digitalocean.com/products/kubernetes/details/supported-gpus/
    Cluster Creation: https://docs.digitalocean.com/products/kubernetes/how-to/create-clusters/

Usage: $0 [OPTIONS]

Options:
    -h, --help              Show this help message

After this completes, run the standard deployment:
    cd ../../../guides/pd-disaggregation
    helmfile apply -e digitalocean

EOF
}

check_do_cluster() {
    print_status "Validating DigitalOcean Kubernetes cluster..."

    # Check if we're on DOKS
    if ! kubectl get nodes -o json | jq -r '.items[0].metadata.labels' | grep -q "doks.digitalocean.com"; then
        print_warning "Not detected as DigitalOcean Kubernetes Service (DOKS)"
        print_warning "This script is optimized for DOKS. Continuing anyway..."
    else
        print_success "DOKS cluster detected"
    fi

    # Check for GPU nodes
    local gpu_nodes
    gpu_nodes=$(kubectl get nodes -l "nvidia.com/gpu" --no-headers 2>/dev/null | wc -l || echo "0")

    if [[ "$gpu_nodes" -lt 2 ]]; then
        print_error "Need at least 2 GPU nodes for P/D disaggregation"
        print_error "Current GPU nodes: $gpu_nodes"
        print_error "Please add GPU nodes to your DOKS cluster"
        exit 1
    fi

    print_success "Found $gpu_nodes GPU node(s) - sufficient for P/D disaggregation"
}

check_gpu_drivers() {
    print_status "Validating GPU drivers and device plugin..."

    # Check NVIDIA device plugin
    if ! kubectl get pods -n nvidia-device-plugin-system -l name=nvidia-device-plugin-ds 2>/dev/null | grep -q Running; then
        print_warning "NVIDIA Device Plugin not found or not running"
        print_status "Installing NVIDIA GPU Operator..."

        # Install GPU Operator using official NVIDIA method
        helm repo add nvidia https://helm.ngc.nvidia.com/nvidia || true
        helm repo update

        kubectl create namespace nvidia-gpu-operator --dry-run=client -o yaml | kubectl apply -f -

        helm upgrade --install gpu-operator nvidia/gpu-operator \
            --namespace nvidia-gpu-operator \
            --set driver.enabled=false \
            --set toolkit.enabled=false \
            --wait \
            --timeout=600s

        print_success "NVIDIA GPU Operator installed"
    else
        print_success "NVIDIA Device Plugin is running"
    fi

    # Validate GPU resources are available
    local gpu_capacity
    gpu_capacity=$(kubectl get nodes -o json | jq -r '.items[].status.capacity."nvidia.com/gpu" // "0"' | awk '{sum += $1} END {print sum}')

    if [[ "$gpu_capacity" -lt 2 ]]; then
        print_error "Insufficient GPU resources. Need at least 2 GPUs, found: $gpu_capacity"
        exit 1
    fi

    print_success "Total GPU capacity: $gpu_capacity"
}

setup_do_storage() {
    print_status "Configuring DigitalOcean Block Storage..."

    # Check if DO CSI driver is available
    if ! kubectl get storageclass do-block-storage 2>/dev/null; then
        print_warning "DigitalOcean Block Storage CSI driver not found"
        print_status "This should be installed automatically on DOKS"
    else
        print_success "DigitalOcean Block Storage available"
    fi

    # Set default storage class if needed
    kubectl annotate storageclass do-block-storage storageclass.kubernetes.io/is-default-class=true --overwrite || true
}

setup_do_networking() {
    print_status "Validating DigitalOcean VPC networking..."

    # Check VPC-native networking (default on new DOKS clusters)
    local vpc_native
    vpc_native=$(kubectl get nodes -o json | jq -r '.items[0].metadata.labels."doks.digitalocean.com/vpc-native" // "unknown"')

    if [[ "$vpc_native" == "true" ]]; then
        print_success "VPC-native networking enabled (recommended)"
    else
        print_warning "VPC-native networking status: $vpc_native"
        print_warning "For best performance, consider upgrading to VPC-native networking"
    fi

    # Verify cluster can reach DigitalOcean services
    if ! kubectl run test-connectivity --image=curlimages/curl --rm -it --restart=Never -- curl -I https://cloud.digitalocean.com 2>/dev/null | grep -q "HTTP/"; then
        print_warning "Could not verify connectivity to DigitalOcean services"
    else
        print_success "DigitalOcean services connectivity verified"
    fi
}

create_do_tolerations() {
    print_status "Setting up DigitalOcean GPU node tolerations..."

    # Create namespace
    kubectl create namespace llm-d-pd --dry-run=client -o yaml | kubectl apply -f -

    print_success "Namespace created, ready for Qwen model deployment"
    print_status "Model configuration: Qwen/Qwen2.5-3B-Instruct (public, no token needed)"
}

main() {
    print_status "Starting DigitalOcean-specific setup for llm-d P/D disaggregation"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                print_error "Unknown argument: $1"
                show_usage
                exit 1
                ;;
        esac
    done

    # Run DO-specific checks and setup
    check_do_cluster
    check_gpu_drivers
    setup_do_storage
    setup_do_networking
    create_do_tolerations

    print_success "DigitalOcean-specific prerequisites completed!"
    echo
    print_status "Next step: Run standard Helm deployment"
    echo "    cd ../../../guides/pd-disaggregation"
    echo "    helmfile apply -e digitalocean"
}

main "$@"