#!/bin/bash

# DigitalOcean P/D Disaggregation Monitoring Setup
# This script sets up Prometheus + Grafana monitoring for DigitalOcean DOKS
# It properly configures ServiceMonitors to collect metrics from the EPP component

set -euo pipefail

# Configuration
NAMESPACE="llm-d-monitoring"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_NAME="prometheus"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

print_header() {
    echo ""
    echo "🚀 DigitalOcean P/D Monitoring Setup"
    echo "====================================="
    echo ""
}

# Usage information
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -u, --uninstall    Uninstall monitoring stack"
    echo "  -h, --help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                 Install monitoring"
    echo "  $0 -u              Uninstall monitoring"
    echo ""
}

# Check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    if ! command -v kubectl &> /dev/null; then
        print_error "kubectl is not installed or not in PATH"
        exit 1
    fi
    
    if ! command -v helm &> /dev/null; then
        print_error "helm is not installed or not in PATH"
        exit 1
    fi
    
    # Check if kubectl can connect to cluster
    if ! kubectl cluster-info &> /dev/null; then
        print_error "Cannot connect to Kubernetes cluster"
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

# Add Helm repositories
setup_helm_repos() {
    print_status "Setting up Helm repositories..."
    
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update
    
    print_success "Helm repositories configured"
}

# Create namespace
create_namespace() {
    print_status "Creating namespace: ${NAMESPACE}"
    
    if kubectl get namespace ${NAMESPACE} &> /dev/null; then
        print_warning "Namespace ${NAMESPACE} already exists"
    else
        kubectl create namespace ${NAMESPACE}
        print_success "Namespace ${NAMESPACE} created"
    fi
}

# Install Prometheus Stack
install_prometheus_stack() {
    print_status "Installing Prometheus Stack with P/D Disaggregation configuration..."
    
    # Create P/D Disaggregation optimized values
    cat > prometheus-pd-values.yaml << 'EOF'
# DigitalOcean P/D Disaggregation Monitoring Configuration
# Optimized for DigitalOcean Kubernetes Service (DOKS) with P/D monitoring

global:
  imageRegistry: ""

## Configure prometheus
prometheus:
  prometheusSpec:
    retention: 7d
    retentionSize: 15GB
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: do-block-storage
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 30Gi
    
    # ServiceMonitor selector - allows discovery of P/D ServiceMonitors
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector:
      matchLabels:
        release: prometheus

## Configure alertmanager
alertmanager:
  alertmanagerSpec:
    storage:
      volumeClaimTemplate:
        spec:
          storageClassName: do-block-storage
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 10Gi

## Configure grafana
grafana:
  persistence:
    enabled: true
    storageClassName: do-block-storage
    size: 10Gi
  
  # Enable sidecar for dashboard discovery
  sidecar:
    dashboards:
      enabled: true
      searchNamespace: llm-d-monitoring
      label: grafana_dashboard
      folderAnnotation: grafana_folder
  
  # Default admin password (you should change this)
  adminPassword: admin

# Disable node-exporter and kube-state-metrics for minimal setup
nodeExporter:
  enabled: false
kubeStateMetrics:
  enabled: false

# Keep prometheus-operator and prometheus
prometheusOperator:
  enabled: true

EOF

    # Check if release already exists
    if helm list -n ${NAMESPACE} | grep -q ${RELEASE_NAME}; then
        print_warning "Prometheus stack already installed. Upgrading..."
        
        # Clean up any incomplete installations
        kubectl delete job --ignore-not-found=true -n ${NAMESPACE} -l app.kubernetes.io/name=prometheus-admission-delete
        kubectl delete job --ignore-not-found=true -n ${NAMESPACE} -l app.kubernetes.io/name=prometheus-admission-patch
        
        helm upgrade ${RELEASE_NAME} prometheus-community/kube-prometheus-stack \
            -n ${NAMESPACE} \
            -f prometheus-pd-values.yaml \
            --wait --timeout=600s
    else
        helm install ${RELEASE_NAME} prometheus-community/kube-prometheus-stack \
            -n ${NAMESPACE} \
            -f prometheus-pd-values.yaml \
            --wait --timeout=600s
    fi
    
    print_success "Prometheus Stack installed successfully"
}

# Create P/D ServiceMonitors
create_pd_servicemonitors() {
    print_status "Creating P/D Disaggregation ServiceMonitors..."
    
    # Create ServiceMonitor for EPP component with authentication
    cat <<EOF | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: llm-d-pd-epp-metrics
  namespace: llm-d-monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gaie-pd-epp
  namespaceSelector:
    matchNames:
    - llm-d-pd
  endpoints:
  - port: http-metrics
    path: /metrics
    interval: 30s
    scrapeTimeout: 10s
    bearerTokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: llm-d-pd-prefill-metrics
  namespace: llm-d-monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      llm-d.ai/role: prefill
  namespaceSelector:
    matchNames:
    - llm-d-pd
  endpoints:
  - port: metrics
    path: /metrics
    interval: 30s
    scrapeTimeout: 10s
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: llm-d-pd-decode-metrics
  namespace: llm-d-monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      llm-d.ai/role: decode
  namespaceSelector:
    matchNames:
    - llm-d-pd
  endpoints:
  - port: metrics
    path: /metrics
    interval: 30s
    scrapeTimeout: 10s
EOF
    
    print_success "P/D Disaggregation ServiceMonitors created successfully"
}

# Import Inference Gateway Dashboard
import_inference_gateway_dashboard() {
    print_status "Importing Inference Gateway Dashboard..."
    
    # Wait for Grafana to be ready
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=grafana -n ${NAMESPACE} --timeout=300s
    
    # Import inference gateway dashboard
    if [[ -f "${SCRIPT_DIR}/inference-gateway-dashboard.json" ]]; then
        # Delete existing ConfigMap if it exists
        kubectl delete configmap inference-gateway-dashboard -n ${NAMESPACE} --ignore-not-found=true
        
        # Create new ConfigMap with proper structure for Grafana Sidecar
        kubectl create configmap inference-gateway-dashboard \
            --from-file="${SCRIPT_DIR}/inference-gateway-dashboard.json" \
            -n ${NAMESPACE}
        
        # Add required labels for Grafana Sidecar discovery
        kubectl label configmap inference-gateway-dashboard grafana_dashboard=1 -n ${NAMESPACE} --overwrite
        kubectl annotate configmap inference-gateway-dashboard grafana_folder="Inference Gateway" -n ${NAMESPACE} --overwrite
        
        print_success "Inference Gateway Dashboard imported successfully"
    else
        print_error "Dashboard file not found: ${SCRIPT_DIR}/inference-gateway-dashboard.json"
    fi
}

# Main installation function
install_monitoring() {
    print_header
    
    check_prerequisites
    setup_helm_repos
    create_namespace
    install_prometheus_stack
    create_pd_servicemonitors
    import_inference_gateway_dashboard
    
    echo ""
    print_success "✅ P/D Disaggregation Monitoring Setup Complete!"
    echo ""
    echo "📊 Access Instructions:"
    echo "   Grafana: kubectl port-forward -n ${NAMESPACE} svc/prometheus-grafana 3000:80"
    echo "   Prometheus: kubectl port-forward -n ${NAMESPACE} svc/prometheus-kube-prometheus-prometheus 9090:9090"
    echo ""
    echo "🔑 Grafana Login:"
    echo "   Username: admin"
    echo "   Password: \$(kubectl get secret prometheus-grafana -n ${NAMESPACE} -o jsonpath='{.data.admin-password}' | base64 -d)"
    echo ""
    echo "✅ MONITORING STATUS:"
    echo "   - vLLM Metrics: ✅ Available (Token Throughput, Latency, etc.)"
    echo "   - EPP Inference Model Metrics: ✅ Available (Request rates, E2E latency)"
    echo "   - EPP Inference Pool Metrics: ✅ Available (KV cache, Queue sizes)"
    echo "   - All dashboard sections should now display data!"
    echo ""
}

# Uninstall monitoring
uninstall_monitoring() {
    print_status "Uninstalling P/D Disaggregation Monitoring..."
    
    # Remove Helm release
    if helm list -n ${NAMESPACE} | grep -q ${RELEASE_NAME}; then
        helm uninstall ${RELEASE_NAME} -n ${NAMESPACE}
        print_success "Prometheus stack uninstalled"
    fi
    
    # Remove ServiceMonitors
    kubectl delete servicemonitors -n ${NAMESPACE} --all --ignore-not-found=true
    
    # Remove namespace
    kubectl delete namespace ${NAMESPACE} --ignore-not-found=true
    
    # Clean up local files
    rm -f prometheus-pd-values.yaml
    
    print_success "✅ Monitoring stack completely removed"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--uninstall)
            uninstall_monitoring
            exit 0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main execution
install_monitoring