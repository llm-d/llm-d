#!/bin/bash

# DigitalOcean P/D Disaggregation Deployment Script
# Minimal setup: 1 Prefill pod + 1 Decode pod (2 GPUs total)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_CONFIGS_DIR="${SCRIPT_DIR}/gpu-configs"
QUICKSTART_DIR="${SCRIPT_DIR}/../../.."
EXAMPLES_DIR="${QUICKSTART_DIR}/examples"
NAMESPACE="llm-d-pd"

# Default values
HUGGINGFACE_TOKEN=""
UNINSTALL=false
INSTALL_MONITORING=false

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_usage() {
    cat << EOF
🚀 DigitalOcean P/D Disaggregation Deployment (Minimal Setup)

Usage: $0 [OPTIONS]

Options:
    -t, --token TOKEN       HuggingFace token (required)
    -m, --monitoring        Install monitoring stack (Prometheus + Grafana)
    -u, --uninstall         Uninstall P/D disaggregation and monitoring (complete cleanup)
    -h, --help              Show this help message

Architecture:
    ┌─────────────────┐    ┌─────────────────┐
    │  Prefill Pod    │    │  Decode Pod     │  
    │  (1 GPU)        │    │  (1 GPU)        │
    │  Handles Input  │    │  Generates Text │
    └─────────────────┘    └─────────────────┘

Examples:
    # Deploy P/D disaggregation
    $0 -t hf_xxxxxxxxxx

    # Deploy with monitoring
    $0 -t hf_xxxxxxxxxx -m

    # Uninstall everything (P/D + monitoring)
    $0 -u

Environment Variables:
    HF_TOKEN               HuggingFace token (alternative to -t)

Note: 
    This deploys minimal P/D disaggregation optimized for DigitalOcean
    with 2 GPU nodes. Uses static configuration files for reliable deployment.
    RDMA is automatically disabled for DigitalOcean compatibility.

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -t|--token)
                HUGGINGFACE_TOKEN="$2"
                shift 2
                ;;
            -m|--monitoring)
                INSTALL_MONITORING=true
                shift
                ;;
            -u|--uninstall)
                UNINSTALL=true
                shift
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

validate_inputs() {
    if [[ "$UNINSTALL" == "true" ]]; then
        return 0
    fi
    
    if [[ -z "$HUGGINGFACE_TOKEN" && -n "${HF_TOKEN:-}" ]]; then
        HUGGINGFACE_TOKEN="$HF_TOKEN"
    fi
    
    if [[ -z "$HUGGINGFACE_TOKEN" ]]; then
        print_error "HuggingFace token is required. Use -t or set HF_TOKEN environment variable"
        exit 1
    fi
    
    if [[ ! -d "${EXAMPLES_DIR}/pd-disaggregation" ]]; then
        print_error "P/D disaggregation example directory not found: ${EXAMPLES_DIR}/pd-disaggregation"
        exit 1
    fi
}

setup_gpu_environment() {
    print_status "Setting up GPU environment..."
    
    # Check if NVIDIA Device Plugin is installed
    if ! kubectl get pods -n nvidia-device-plugin -l app.kubernetes.io/name=nvidia-device-plugin &>/dev/null; then
        print_warning "NVIDIA Device Plugin not found, installing..."
        "${SCRIPT_DIR}/setup-gpu-cluster.sh" --skip-cluster-create --force-reinstall
    else
        print_success "NVIDIA Device Plugin already installed"
    fi
}

check_prerequisites() {
    print_status "Checking prerequisites..."
    
    local missing_tools=()
    
    for tool in kubectl helm helmfile; do
        if ! command -v $tool &> /dev/null; then
            missing_tools+=("$tool")
        fi
    done
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        print_error "Missing required tools: ${missing_tools[*]}"
        print_error "Please run: cd ${QUICKSTART_DIR}/dependencies && ./install-deps.sh"
        exit 1
    fi
    
    if ! kubectl cluster-info &> /dev/null; then
        print_error "Cannot connect to Kubernetes cluster"
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

setup_gateway() {
    print_status "Setting up gateway infrastructure..."
    
    cd "$QUICKSTART_DIR"
    
    # Install gateway provider dependencies
    if [[ -f "gateway-control-plane-providers/install-gateway-provider-dependencies.sh" ]]; then
        print_status "Installing gateway provider dependencies..."
        cd gateway-control-plane-providers
        ./install-gateway-provider-dependencies.sh || print_warning "Gateway dependencies had issues"
        cd "$QUICKSTART_DIR"
    fi
    
    # Deploy Istio gateway
    print_status "Deploying Istio gateway infrastructure..."
    cd gateway-control-plane-providers
    helmfile -f istio.helmfile.yaml apply || {
        print_error "Failed to deploy Istio gateway"
        exit 1
    }
    
    print_success "Gateway infrastructure setup completed"
}

create_hf_secret() {
    print_status "Creating HuggingFace token secret..."
    
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic llm-d-hf-token \
        --from-literal=HF_TOKEN="$HUGGINGFACE_TOKEN" \
        -n "$NAMESPACE" \
        --dry-run=client -o yaml | kubectl apply -f -
        
    print_success "HuggingFace token secret created"
}

# No longer needed - using static digitalocean-values.yaml file

deploy_pd_disaggregation() {
    print_status "Deploying P/D disaggregation..."
    print_status "Architecture: 1 Prefill Pod (1 GPU) + 1 Decode Pod (1 GPU)"
    
    create_hf_secret
    
    cd "${EXAMPLES_DIR}/pd-disaggregation"
    
    # Deploy using DigitalOcean environment (namespace is set in helmfile.yaml.gotmpl)
    if NAMESPACE="${NAMESPACE}" helmfile apply -e digitalocean; then
        print_success "P/D disaggregation deployed successfully"
    else
        print_error "P/D disaggregation deployment failed"
        exit 1
    fi
}

uninstall_monitoring() {
    print_status "Uninstalling P/D Disaggregation monitoring stack..."
    
    local monitoring_script="${SCRIPT_DIR}/monitoring/setup-monitoring.sh"
    
    if [[ -f "$monitoring_script" ]]; then
        chmod +x "$monitoring_script"
        if "$monitoring_script" --uninstall; then
            print_success "Monitoring stack uninstalled successfully"
        else
            print_warning "Issues uninstalling monitoring stack"
        fi
    else
        print_warning "Monitoring script not found, attempting manual cleanup..."
        
        # Manual cleanup of monitoring resources
        if kubectl get namespace llm-d-monitoring &>/dev/null; then
            print_status "Removing monitoring namespace and resources..."
            
            # Remove ServiceMonitors first
            kubectl delete servicemonitors -n llm-d-monitoring --all --ignore-not-found=true
            
            # Remove Prometheus stack
            if helm list -n llm-d-monitoring | grep -q prometheus; then
                helm uninstall prometheus -n llm-d-monitoring --ignore-not-found || print_warning "Issues removing Prometheus"
            fi
            
            # Remove namespace
            kubectl delete namespace llm-d-monitoring --ignore-not-found=true
            
            print_success "Manual monitoring cleanup completed"
        else
            print_warning "No monitoring components found"
        fi
    fi
}

uninstall_pd_disaggregation() {
    print_status "Uninstalling P/D disaggregation and monitoring..."
    
    # Always try to uninstall monitoring first (if it exists)
    uninstall_monitoring
    
    # Uninstall P/D disaggregation
    cd "${EXAMPLES_DIR}/pd-disaggregation"
    NAMESPACE="${NAMESPACE}" helmfile destroy -e digitalocean || print_warning "Issues during P/D uninstall"
    
    # Cleanup gateway
    cd "${QUICKSTART_DIR}/gateway-control-plane-providers"
    helmfile -f istio.helmfile.yaml destroy || print_warning "Issues destroying gateway"
    
    print_success "🎉 Complete uninstallation finished!"
}

setup_monitoring() {
    print_status "Setting up P/D Disaggregation monitoring stack..."
    
    local monitoring_script="${SCRIPT_DIR}/monitoring/setup-monitoring.sh"
    
    if [[ ! -f "$monitoring_script" ]]; then
        print_error "Monitoring setup script not found at: $monitoring_script"
        exit 1
    fi
    
    # Make sure the monitoring script is executable
    chmod +x "$monitoring_script"
    
    # Run the monitoring setup script
    if "$monitoring_script"; then
        print_success "Monitoring stack installed successfully"
        echo ""
        echo "📊 Monitoring Access:"
        echo "  Grafana: kubectl port-forward -n llm-d-monitoring svc/prometheus-grafana 3000:80"
        echo "  URL: http://localhost:3000"
        echo "  Username: admin"
        echo "  Password: kubectl get secret prometheus-grafana -n llm-d-monitoring -o jsonpath=\"{.data.admin-password}\" | base64 -d"
        echo ""
    else
        print_error "Monitoring setup failed"
        exit 1
    fi
}

wait_for_deployment() {
    print_status "Waiting for P/D disaggregation to be ready..."
    
    local max_wait=300
    local count=0
    while ! kubectl get namespace "$NAMESPACE" &> /dev/null && [[ $count -lt $max_wait ]]; do
        sleep 2
        ((count+=2))
    done
    
    if [[ $count -ge $max_wait ]]; then
        print_error "Timeout waiting for namespace"
        return 1
    fi
    
    print_status "Waiting for pods to be ready (may take several minutes for model download)..."
    kubectl wait --for=condition=available deployment --all -n "$NAMESPACE" --timeout=900s || {
        print_warning "Some deployments may still be starting"
        print_status "Current status:"
        kubectl get pods -n "$NAMESPACE"
    }
    
    print_success "P/D disaggregation is ready"
}

show_status() {
    echo ""
    echo "=========================================="
    echo "🎉 P/D Disaggregation Status (Minimal)"
    echo "=========================================="
    echo ""
    
    print_status "Pods in namespace: $NAMESPACE"
    kubectl get pods -n "$NAMESPACE" -o wide
    echo ""
    
    print_status "Services:"
    kubectl get svc -n "$NAMESPACE"
    echo ""
    
    print_status "Gateway:"
    kubectl get gateway -n "$NAMESPACE"
    echo ""
    
    echo "🧪 Test Commands:"
    echo "  # Port forward to test:"
    echo "  kubectl port-forward -n $NAMESPACE svc/infra-pd-inference-gateway-istio 8080:80"
    echo ""
    echo "  # Test inference:"
    echo "  curl -X POST http://localhost:8080/v1/chat/completions \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"model\": \"meta-llama/Llama-3.2-3B-Instruct\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello P/D!\"}], \"max_tokens\": 50}'"
    echo ""
    echo "  # Check logs:"
    echo "  kubectl logs -n $NAMESPACE -l llm-d.ai/role=prefill"
    echo "  kubectl logs -n $NAMESPACE -l llm-d.ai/role=decode"
    echo ""
}

main() {
    echo "🚀 DigitalOcean P/D Disaggregation Deployment (Minimal)"
    echo "======================================================="
    echo ""
    
    parse_args "$@"
    validate_inputs
    check_prerequisites
    
    if [[ "$UNINSTALL" == "true" ]]; then
        uninstall_pd_disaggregation
        return 0
    fi
    
    print_status "Deployment Configuration:"
    print_status "  Architecture: Minimal P/D Disaggregation"
    print_status "  Prefill Pods: 1 (1 GPU)"
    print_status "  Decode Pods: 1 (1 GPU)"
    print_status "  Total GPUs: 2"
    print_status "  RDMA: Disabled (DigitalOcean compatible)"
    echo ""
    
    setup_gpu_environment
    setup_gateway
    deploy_pd_disaggregation
    wait_for_deployment
    
    # Install monitoring if requested
    if [[ "$INSTALL_MONITORING" == "true" ]]; then
        setup_monitoring
    fi
    
    show_status
    
    print_success "🎉 P/D Disaggregation deployment completed!"
    echo ""
    echo "💡 Architecture Summary:"
    echo "  ┌─────────────────┐    ┌─────────────────┐"  
    echo "  │  Prefill Pod    │    │  Decode Pod     │"
    echo "  │  (1 GPU Node)   │    │  (1 GPU Node)   │"
    echo "  │  Processes      │    │  Generates      │"
    echo "  │  Input Prompts  │    │  Output Text    │"
    echo "  └─────────────────┘    └─────────────────┘"
    echo ""
    echo "This minimal setup uses exactly 2 GPUs across 2 nodes for"
    echo "efficient P/D disaggregation on DigitalOcean."
    echo ""
}

main "$@"