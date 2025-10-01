#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
# Use central monitoring namespace by default (configurable via MONITORING_NAMESPACE env var)
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-}"
NAMESPACE_EXPLICITLY_SET=false
ACTION="install"
KUBERNETES_CONTEXT=""
DEBUG=""
CENTRAL_MODE=true

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Install or uninstall Prometheus and Grafana stack for llm-d metrics collection.

Options:
  -n, --namespace NAME        Monitoring namespace (default: llm-d-monitoring for central mode)
  -u, --uninstall             Uninstall Prometheus and Grafana stack
  -d, --debug                 Add debug mode to the helm install
  -g, --context               Supply a specific Kubernetes context
  -i, --individual            Enable individual user monitoring mode (requires -n or MONITORING_NAMESPACE)
  -h, --help                  Show this help and exit

Environment Variables:
  MONITORING_NAMESPACE        Override default monitoring namespace

Examples:
  $(basename "$0")                              # Install central monitoring in llm-d-monitoring (watches all namespaces)
  $(basename "$0") -n monitoring                # Install central monitoring in 'monitoring' namespace
  $(basename "$0") -u                           # Uninstall Prometheus/Grafana stack
  $(basename "$0") -i -n my-monitoring          # Install individual monitoring in specified namespace
  MONITORING_NAMESPACE=my-monitoring $(basename "$0") -i  # Individual mode via env var
EOF
}

# ANSI colour helpers and functions
COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_YELLOW=$'\e[33m'
COLOR_RED=$'\e[31m'
COLOR_BLUE=$'\e[34m'

log_info() {
  echo "${COLOR_BLUE}ℹ️  $*${COLOR_RESET}"
}

log_success() {
  echo "${COLOR_GREEN}✅ $*${COLOR_RESET}"
}

log_error() {
  echo "${COLOR_RED}❌ $*${COLOR_RESET}" >&2
}

fail() { log_error "$*"; exit 1; }

### UTILITIES ###
check_cmd() {
  command -v "$1" &>/dev/null || fail "Required command not found: $1"
}

check_dependencies() {
  local required_cmds=(helm kubectl)
  for cmd in "${required_cmds[@]}"; do
    check_cmd "$cmd"
  done
}

check_cluster_reachability() {
  if kubectl cluster-info &> /dev/null; then
    log_info "kubectl can reach to a running Kubernetes cluster."
  else
    fail "kubectl cannot reach any running Kubernetes cluster. The installer requires a running cluster"
  fi
}


parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace)                  MONITORING_NAMESPACE="$2"; NAMESPACE_EXPLICITLY_SET=true; shift 2 ;;
      -u|--uninstall)                  ACTION="uninstall"; shift ;;
      -d|--debug)                      DEBUG="--debug"; shift;;
      -g|--context)                    KUBERNETES_CONTEXT="$2"; shift 2 ;;
      -i|--individual)                 CENTRAL_MODE=false; shift ;;
      -h|--help)                       print_help; exit 0 ;;
      *)                               fail "Unknown option: $1" ;;
    esac
  done
}

setup_env() {
  if [[ ! -z $KUBERNETES_CONTEXT ]]; then
    if [[ ! -f $KUBERNETES_CONTEXT ]]; then
      log_error "Error, the context file \"$KUBERNETES_CONTEXT\", passed via command-line option, does not exist!"
      exit 1
    fi
    KCMD="kubectl --kubeconfig $KUBERNETES_CONTEXT"
    HCMD="helm --kubeconfig $KUBERNETES_CONTEXT"
  else
    KCMD="kubectl"
    HCMD="helm"
  fi

  # Set up monitoring namespace and labels based on mode
  if [[ "$CENTRAL_MODE" == "true" ]]; then
    # Central mode: use specified namespace or default to llm-d-monitoring
    if [[ -z "$MONITORING_NAMESPACE" ]]; then
      MONITORING_NAMESPACE="llm-d-monitoring"
    fi
    MONITORING_LABEL_KEY=""
    MONITORING_LABEL_VALUE=""
  else
    # Individual mode: require explicit namespace
    if [[ -z "$MONITORING_NAMESPACE" ]]; then
      fail "Individual monitoring mode (-i) requires a namespace to be specified via -n flag or MONITORING_NAMESPACE environment variable"
    fi
    MONITORING_LABEL_KEY="monitoring-ns"
    MONITORING_LABEL_VALUE="${MONITORING_NAMESPACE}"
  fi
}

is_openshift() {
  # Check for OpenShift-specific resources
  if $KCMD get clusterversion &>/dev/null; then
    return 0
  fi
  return 1
}

check_servicemonitor_crd() {
  log_info "🔍 Checking for ServiceMonitor CRD (monitoring.coreos.com)..."
  if ! $KCMD get crd servicemonitors.monitoring.coreos.com &>/dev/null; then
    log_info "⚠️ ServiceMonitor CRD (monitoring.coreos.com) not found - will be installed with Prometheus stack"
    return 1
  fi

  API_VERSION=$($KCMD get crd servicemonitors.monitoring.coreos.com -o jsonpath='{.spec.versions[?(@.served)].name}' 2>/dev/null || echo "")

  if [[ -z "$API_VERSION" ]]; then
    log_info "⚠️ Could not determine ServiceMonitor CRD API version"
    return 1
  fi

  if [[ "$API_VERSION" == "v1" ]]; then
    log_success "ServiceMonitor CRD (monitoring.coreos.com/v1) found - using existing installation"
    return 0
  else
    log_info "⚠️ Found ServiceMonitor CRD but with unexpected API version: ${API_VERSION}"
    return 1
  fi
}

check_existing_node_exporter() {
  log_info "🔍 Checking for existing node-exporter installations..."
  # Shared clusters with existing monitoring commonly have pre-existing node-exporters,
  # and it's not necessary to have multiple of these running (they would conflict on port 9100)
  # Check for existing node-exporter pods in other namespaces
  local existing_exporters=$($KCMD get pods --all-namespaces -l app=node-exporter -o name 2>/dev/null | wc -l)

  if [[ $existing_exporters -eq 0 ]]; then
    # Also check for common node-exporter naming patterns
    existing_exporters=$($KCMD get pods --all-namespaces | grep -E "node-exporter|nodeexporter" | grep -v "prometheus-${MONITORING_NAMESPACE}" | wc -l)
  fi

  if [[ $existing_exporters -gt 0 ]]; then
    log_info "⚠️ Found $existing_exporters existing node-exporter pod(s) in other namespaces"
    log_info "ℹ️ Node-exporter will be disabled to avoid port conflicts (port 9100)"
    # Show which namespaces have node-exporters
    log_info "📋 Existing node-exporter pods:"
    $KCMD get pods --all-namespaces | grep -E "node-exporter|nodeexporter" | grep -v "prometheus-${MONITORING_NAMESPACE}" | head -3
    return 0  # Existing node-exporters found, should disable
  else
    log_info "✅ No existing node-exporter installations detected"
    return 1  # No existing node-exporters, can enable
  fi

}

check_openshift_monitoring() {
  if ! is_openshift; then
    return 0
  fi

  log_info "🔍 Checking OpenShift user workload monitoring configuration..."

  # Check if user workload monitoring is enabled
  if $KCMD get configmap cluster-monitoring-config -n openshift-monitoring -o yaml 2>/dev/null | grep -q "enableUserWorkload: true"; then
    log_success "✅ OpenShift user workload monitoring is properly configured"
    return 0
  fi

  log_info "⚠️ OpenShift user workload monitoring is not enabled"
  log_info "ℹ️ Enabling user workload monitoring allows metrics collection for the llm-d chart."

  local monitoring_yaml=$(cat <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
EOF
)

  # Prompt the user
  log_info "📜 The following ConfigMap will be applied to enable user workload monitoring:"
  echo "$monitoring_yaml"
  read -p "Would you like to apply this ConfigMap to enable user workload monitoring? (y/N): " response
  case "$response" in
    [yY][eE][sS]|[yY])
      log_info "🚀 Applying ConfigMap to enable user workload monitoring..."
      echo "$monitoring_yaml" | oc create -f -
      if [[ $? -eq 0 ]]; then
        log_success "✅ OpenShift user workload monitoring enabled"
        return 0
      else
        log_error "❌ Failed to apply ConfigMap. Metrics collection may not work."
        return 1
      fi
      ;;
    *)
      log_info "⚠️ User chose not to enable user workload monitoring."
      log_info "⚠️ Metrics collection may not work properly in OpenShift without user workload monitoring enabled."
      return 1
      ;;
  esac
}

install_prometheus_grafana() {
  log_info "🌱 Provisioning Prometheus operator…"

  if ! $KCMD get namespace "${MONITORING_NAMESPACE}" &>/dev/null; then
    log_info "📦 Creating monitoring namespace..."
    $KCMD create namespace "${MONITORING_NAMESPACE}"
  else
    log_info "📦 Monitoring namespace already exists"
  fi

  if ! $HCMD repo list 2>/dev/null | grep -q "prometheus-community"; then
    log_info "📚 Adding prometheus-community helm repo..."
    $HCMD repo add prometheus-community https://prometheus-community.github.io/helm-charts
    $HCMD repo update
  fi

  # Check if release already exists using the same naming scheme
  RELEASE_NAME="prometheus-${MONITORING_NAMESPACE}"

  if $HCMD list -n "${MONITORING_NAMESPACE}" | grep -q "${RELEASE_NAME}"; then
    log_info "⚠️ Prometheus stack already installed as '${RELEASE_NAME}' in ${MONITORING_NAMESPACE} namespace"
    if [[ "$CENTRAL_MODE" == "true" ]]; then
      log_info "ℹ️ To update existing installation to central mode, first uninstall with: $0 -u -n ${MONITORING_NAMESPACE}"
    else
      log_info "ℹ️ To update configuration, first uninstall with: $0 -u -n ${MONITORING_NAMESPACE}"
    fi
    return 0
  fi

  # Check if CRDs already exist (installed by another user)
  if check_servicemonitor_crd; then
    log_info "🔄 ServiceMonitor CRDs already exist - installing without CRDs to avoid conflicts"
    CRD_INSTALL_FLAG="--skip-crds"
  else
    log_info "🆕 Installing Prometheus stack with CRDs"
    CRD_INSTALL_FLAG=""
  fi

  # Check for existing node-exporters and determine if we should disable them
  local DISABLE_NODE_EXPORTER=""
  if check_existing_node_exporter; then
    DISABLE_NODE_EXPORTER="nodeExporter:\n  enabled: false"
  fi

  log_info "🚀 Installing Prometheus stack in namespace ${MONITORING_NAMESPACE}..."

  if [[ "$CENTRAL_MODE" == "true" ]]; then
    # Central mode: Monitor all namespaces without label restrictions
    cat <<EOF > /tmp/prometheus-values.yaml
grafana:
  adminPassword: admin
  service:
    type: ClusterIP
    sessionAffinity: ""
prometheus:
  service:
    type: ClusterIP
    sessionAffinity: ""
  prometheusSpec:
    # Central monitoring: watch all ServiceMonitors and PodMonitors in all namespaces
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector: {}
    serviceMonitorNamespaceSelector: {}
    podMonitorSelectorNilUsesHelmValues: false
    podMonitorSelector: {}
    podMonitorNamespaceSelector: {}
    maximumStartupDurationSeconds: 300
    # Higher resource limits for central monitoring
    resources:
      limits:
        memory: 8Gi
        cpu: 4000m
      requests:
        memory: 4Gi
        cpu: 1000m
$(if [[ -n "$DISABLE_NODE_EXPORTER" ]]; then echo -e "$DISABLE_NODE_EXPORTER"; fi)
EOF
  else
    # Individual mode: Monitor only user's labeled namespaces
    cat <<EOF > /tmp/prometheus-values.yaml
grafana:
  adminPassword: admin
  service:
    type: ClusterIP
    sessionAffinity: ""
prometheus:
  service:
    type: ClusterIP
    sessionAffinity: ""
  prometheusSpec:
    # Limit monitoring to user's namespaces for multi-tenancy
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector:
      matchLabels:
        ${MONITORING_LABEL_KEY}: "${MONITORING_LABEL_VALUE}"
    serviceMonitorNamespaceSelector:
      matchLabels:
        ${MONITORING_LABEL_KEY}: "${MONITORING_LABEL_VALUE}"
    podMonitorSelectorNilUsesHelmValues: false
    podMonitorSelector:
      matchLabels:
        ${MONITORING_LABEL_KEY}: "${MONITORING_LABEL_VALUE}"
    podMonitorNamespaceSelector:
      matchLabels:
        ${MONITORING_LABEL_KEY}: "${MONITORING_LABEL_VALUE}"
    maximumStartupDurationSeconds: 300
    # Resource limits for individual monitoring
    resources:
      limits:
        memory: 4Gi
        cpu: 2000m
      requests:
        memory: 2Gi
        cpu: 500m
$(if [[ -n "$DISABLE_NODE_EXPORTER" ]]; then echo -e "$DISABLE_NODE_EXPORTER"; fi)
EOF
  fi

  # Use unique release name based on namespace to avoid conflicts
  RELEASE_NAME="prometheus-${MONITORING_NAMESPACE}"

  # Apply CRDs first without validation for int32 v int64 unrecognized format
  if [ "${CRD_INSTALL_FLAG:-}" != "--skip-crds" ]; then
    "$HCMD" show crds prometheus-community/kube-prometheus-stack \
    | "$KCMD" apply --server-side --validate=false -f -
  fi

  # Always apply without CRDs because they were conditionally installed before
  $HCMD install "${RELEASE_NAME}" prometheus-community/kube-prometheus-stack \
    --namespace "${MONITORING_NAMESPACE}" \
    ${DEBUG} \
    --skip-crds \
    -f /tmp/prometheus-values.yaml

  rm -f /tmp/prometheus-values.yaml

  log_info "⏳ Waiting for Prometheus stack pods to be ready..."
  $KCMD wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n "${MONITORING_NAMESPACE}" --timeout=300s || true
  $KCMD wait --for=condition=ready pod -l app.kubernetes.io/name=grafana -n "${MONITORING_NAMESPACE}" --timeout=300s || true

  log_success "🚀 Prometheus and Grafana installed."

  # Display access information
  log_info "📊 Access Information:"
  log_info "   Prometheus: kubectl port-forward -n ${MONITORING_NAMESPACE} svc/prometheus-kube-prometheus-prometheus 9090:9090"
  log_info "   Grafana: kubectl port-forward -n ${MONITORING_NAMESPACE} svc/prometheus-grafana 3000:80"
  log_info "   Grafana admin password: admin"
  log_info ""
  log_info "📋 Monitoring Configuration:"
  if [[ "$CENTRAL_MODE" == "true" ]]; then
    log_info "   🌐 Central monitoring: This Prometheus monitors ALL ServiceMonitors and PodMonitors in ALL namespaces"
    log_info "   No namespace labeling is required - all metrics will be collected automatically"
    log_info "   ⚠️  This mode should only be used by cluster administrators or in single-tenant environments"
  else
    log_info "   👤 Individual monitoring: This Prometheus monitors resources labeled with '${MONITORING_LABEL_KEY}: ${MONITORING_LABEL_VALUE}'"
    log_info "   To enable monitoring for your deployments, add this label to your namespaces:"
    log_info "   kubectl label namespace <namespace> ${MONITORING_LABEL_KEY}=${MONITORING_LABEL_VALUE}"
  fi
}

install() {
  if is_openshift; then
    log_info "🔍 OpenShift detected - checking user workload monitoring..."
    if ! check_openshift_monitoring; then
      log_info "⚠️ Metrics collection may not work properly in OpenShift without user workload monitoring enabled."
    fi
    # No Prometheus installation needed if OpenShift monitoring is properly configured
    log_info "ℹ️ Using OpenShift's built-in monitoring stack. No additional Prometheus installation needed."
    log_success "🎉 OpenShift monitoring configuration complete."
  else
    log_info "🔍 Checking for existing ServiceMonitor CRD..."
    if check_servicemonitor_crd; then
      log_info "✅ ServiceMonitor CRD found. Installing namespace-scoped Prometheus stack..."
    else
      log_info "⚠️ ServiceMonitor CRD not found. Installing Prometheus stack with CRDs..."
    fi
    install_prometheus_grafana
    log_success "🎉 Prometheus and Grafana installation complete."
  fi
}

uninstall() {
  log_info "🗑️ Uninstalling Prometheus and Grafana stack..."

  # Use the same release naming scheme as install
  RELEASE_NAME="prometheus-${MONITORING_NAMESPACE}"

  if $HCMD list -n "${MONITORING_NAMESPACE}" | grep -q "${RELEASE_NAME}" 2>/dev/null; then
    log_info "🗑️ Uninstalling Prometheus helm release '${RELEASE_NAME}'..."
    $HCMD uninstall "${RELEASE_NAME}" --namespace "${MONITORING_NAMESPACE}" || true
  fi

  log_info "🗑️ Deleting monitoring namespace..."
  $KCMD delete namespace "${MONITORING_NAMESPACE}" --ignore-not-found || true

  # Check if we should delete the ServiceMonitor CRD (only if no other Prometheus installations exist)
  if ! $KCMD get crd servicemonitors.monitoring.coreos.com &>/dev/null; then
    log_info "ℹ️ ServiceMonitor CRD not found (already deleted or never installed)"
  else
    log_info "ℹ️ ServiceMonitor CRD still exists (may be used by other monitoring installations)"
    log_info "ℹ️ To manually delete: kubectl delete crd servicemonitors.monitoring.coreos.com"
  fi

  log_success "💀 Uninstallation complete"
}

main() {
  parse_args "$@"
  setup_env
  check_dependencies
  check_cluster_reachability

  if [[ "$ACTION" == "install" ]]; then
    install
  elif [[ "$ACTION" == "uninstall" ]]; then
    uninstall
  else
    fail "Unknown action: $ACTION"
  fi
}

main "$@"
