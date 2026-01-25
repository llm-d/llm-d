#!/usr/bin/env bash
# Load llm-d-specific Grafana dashboards into Kubernetes ConfigMaps

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Configuration
NAMESPACE="${1:-llm-d-monitoring}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARDS_DIR="${SCRIPT_DIR}/../grafana/dashboards"

log_info "Loading llm-d dashboards into namespace: ${NAMESPACE}"

# Check if namespace exists
if ! kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    log_error "Namespace ${NAMESPACE} does not exist"
    exit 1
fi

# Check if dashboard directory exists
if [ ! -d "$DASHBOARDS_DIR" ]; then
    log_error "Dashboard directory not found: ${DASHBOARDS_DIR}"
    exit 1
fi

# Load each dashboard
for dashboard_file in "${DASHBOARDS_DIR}"/*.json; do
    if [ ! -f "$dashboard_file" ]; then
        continue
    fi

    dashboard_name=$(basename "$dashboard_file" .json)
    configmap_name="llmd-${dashboard_name}"

    log_info "Loading dashboard: ${dashboard_name}"

    # Create ConfigMap with dashboard JSON
    kubectl create configmap "${configmap_name}" \
        --from-file="${dashboard_name}.json=${dashboard_file}" \
        --namespace="${NAMESPACE}" \
        --dry-run=client -o yaml | \
    kubectl label -f - \
        grafana_dashboard=1 \
        --local --dry-run=client -o yaml | \
    kubectl apply -f -

    if [ $? -eq 0 ]; then
        log_success "Dashboard ${dashboard_name} loaded"
    else
        log_error "Failed to load dashboard ${dashboard_name}"
    fi
done

log_success "All dashboards loaded successfully"
log_info "Grafana will automatically discover and load these dashboards within 30 seconds"
