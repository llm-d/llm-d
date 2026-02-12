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
ENABLE_TLS=false

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
  -t, --enable-tls            Enable HTTPS/TLS for Prometheus (generates self-signed certificates)
  -h, --help                  Show this help and exit

Environment Variables:
  MONITORING_NAMESPACE        Override default monitoring namespace

Examples:
  $(basename "$0")                              # Install central monitoring in llm-d-monitoring (watches all namespaces)
  $(basename "$0") -n monitoring                # Install central monitoring in 'monitoring' namespace
  $(basename "$0") -t                           # Install with HTTPS/TLS enabled
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
      -t|--enable-tls)                 ENABLE_TLS=true; shift ;;
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

  if [[ "$CENTRAL_MODE" == "true" ]]; then
    if [[ -z "$MONITORING_NAMESPACE" ]]; then
      MONITORING_NAMESPACE="llm-d-monitoring"
    fi
    MONITORING_LABEL_KEY=""
    MONITORING_LABEL_VALUE=""
  else
    if [[ -z "$MONITORING_NAMESPACE" ]]; then
      fail "Individual monitoring mode (-i) requires a namespace to be specified via -n flag or MONITORING_NAMESPACE environment variable"
    fi
    MONITORING_LABEL_KEY="monitoring-ns"
    MONITORING_LABEL_VALUE="${MONITORING_NAMESPACE}"
  fi
}

is_openshift() {
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
  local existing_exporters=$($KCMD get pods --all-namespaces -l app=node-exporter -o name 2>/dev/null | wc -l)

  if [[ $existing_exporters -eq 0 ]]; then
    existing_exporters=$($KCMD get pods --all-namespaces | grep -E "node-exporter|nodeexporter" | grep -v "prometheus-${MONITORING_NAMESPACE}" | wc -l)
  fi

  if [[ $existing_exporters -gt 0 ]]; then
    log_info "⚠️ Found $existing_exporters existing node-exporter pod(s) in other namespaces"
    log_info "ℹ️ Node-exporter will be disabled to avoid port conflicts (port 9100)"
    log_info "📋 Existing node-exporter pods:"
    $KCMD get pods --all-namespaces | grep -E "node-exporter|nodeexporter" | grep -v "prometheus-${MONITORING_NAMESPACE}" | head -3
    return 0
  else
    log_info "✅ No existing node-exporter installations detected"
    return 1
  fi
}

check_prometheus_operator() {
  log_info "🔍 Checking for existing Prometheus operator installations..."
  local existing_operators=$($KCMD get pods --all-namespaces -l app.kubernetes.io/name=prometheus-operator -o name 2>/dev/null | wc -l)

  if [[ $existing_operators -eq 0 ]]; then
    existing_operators=$($KCMD get pods --all-namespaces | grep -E "prometheus-operator" | grep -v "${MONITORING_NAMESPACE}" | wc -l)
  fi

  if [[ $existing_operators -gt 0 ]]; then
    log_info "⚠️ Found $existing_operators existing Prometheus operator pod(s) in other namespaces"
    log_info "ℹ️ Prometheus operator will be disabled to avoid resource conflicts"
    log_info "📋 Existing Prometheus operator pods:"
    $KCMD get pods --all-namespaces | grep -E "prometheus-operator" | grep -v "${MONITORING_NAMESPACE}" | head -3

    log_info "🔍 Detecting Prometheus operator version..."
    local operator_image=$($KCMD get pods --all-namespaces -l app.kubernetes.io/name=prometheus-operator -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null)
    if [[ -z "$operator_image" ]]; then
      operator_image=$($KCMD get pods --all-namespaces -o yaml | grep -E "prometheus-operator" | grep "image:" | head -1 | awk '{print $2}')
    fi

    if [[ -n "$operator_image" ]]; then
      log_info "📦 Found operator image: ${operator_image}"
      local operator_version=$(echo "$operator_image" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | sed 's/v//')

      if [[ -n "$operator_version" ]]; then
        log_info "📌 Operator version: v${operator_version}"
        export DETECTED_OPERATOR_VERSION="$operator_version"
      else
        log_info "⚠️ Could not parse operator version from image tag"
      fi
    fi

    return 0
  else
    log_info "✅ No existing Prometheus operator installations detected"
    return 1
  fi
}

setup_tls_certificates() {
  if [[ "$ENABLE_TLS" != "true" ]]; then
    return 0
  fi

  log_info "🔐 Setting up TLS certificates for Prometheus..."

  # Get the directory where this script is located
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
  CERT_SCRIPT="${SCRIPT_DIR}/generate-prometheus-tls-certs.sh"

  if [[ ! -f "$CERT_SCRIPT" ]]; then
    log_error "TLS certificate generation script not found at: $CERT_SCRIPT"
    fail "Please ensure generate-prometheus-tls-certs.sh exists in the scripts directory"
  fi

  # Check if certificates already exist
  if $KCMD get secret prometheus-web-tls -n "${MONITORING_NAMESPACE}" &>/dev/null; then
    log_info "✅ TLS certificates already exist in namespace ${MONITORING_NAMESPACE}"
    return 0
  fi

  # Generate certificates
  log_info "📜 Generating TLS certificates..."
  if [[ ! -z $KUBERNETES_CONTEXT ]]; then
    MONITORING_NAMESPACE="${MONITORING_NAMESPACE}" "$CERT_SCRIPT" -g "$KUBERNETES_CONTEXT"
  else
    MONITORING_NAMESPACE="${MONITORING_NAMESPACE}" "$CERT_SCRIPT"
  fi

  if [[ $? -ne 0 ]]; then
    fail "Failed to generate TLS certificates"
  fi

  log_success "TLS certificates generated and stored in Kubernetes secret"
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

  # Setup TLS certificates if enabled
  setup_tls_certificates

  if ! $HCMD repo list 2>/dev/null | grep -q "prometheus-community"; then
    log_info "📚 Adding prometheus-community helm repo..."
    $HCMD repo add prometheus-community https://prometheus-community.github.io/helm-charts
    $HCMD repo update
  fi

  # Must use 'llmd' not 'llm-d' to avoid Kubernetes 63-char service name limit
  RELEASE_NAME="llmd"

  if $HCMD list -n "${MONITORING_NAMESPACE}" | grep -q "${RELEASE_NAME}"; then
    log_info "⚠️ Prometheus stack already installed as '${RELEASE_NAME}' in ${MONITORING_NAMESPACE} namespace"
    if [[ "$CENTRAL_MODE" == "true" ]]; then
      log_info "ℹ️ To update existing installation to central mode, first uninstall with: $0 -u -n ${MONITORING_NAMESPACE}"
    else
      log_info "ℹ️ To update configuration, first uninstall with: $0 -u -n ${MONITORING_NAMESPACE}"
    fi
    return 0
  fi

  local DISABLE_PROMETHEUS_OPERATOR=""
  local PROMETHEUS_IMAGE_CONFIG=""
  local CHART_VERSION_FLAG=""
  local OPERATOR_EXISTS=false
  if check_prometheus_operator; then
    DISABLE_PROMETHEUS_OPERATOR="prometheusOperator:\n  enabled: false"
    OPERATOR_EXISTS=true

    if [[ -n "$DETECTED_OPERATOR_VERSION" ]]; then
      local major_minor=$(echo "$DETECTED_OPERATOR_VERSION" | cut -d. -f1,2)
      local major=$(echo "$major_minor" | cut -d. -f1)
      local minor=$(echo "$major_minor" | cut -d. -f2)

      if [[ "$major" -eq 0 ]] && [[ "$minor" -lt 77 ]]; then
        log_info "🔧 Operator v${DETECTED_OPERATOR_VERSION} detected - using compatible Prometheus v2.54.1 and chart v62.x"
        PROMETHEUS_IMAGE_CONFIG="    image:\n      registry: quay.io\n      repository: prometheus/prometheus\n      tag: v2.54.1"
        CHART_VERSION_FLAG="--version 62.7.0"
      else
        log_info "✅ Operator v${DETECTED_OPERATOR_VERSION} supports Prometheus v3.x - using default chart version"
      fi
    else
      log_info "⚠️ Could not detect operator version - using conservative Prometheus v2.54.1 and chart v62.x"
      PROMETHEUS_IMAGE_CONFIG="    image:\n      registry: quay.io\n      repository: prometheus/prometheus\n      tag: v2.54.1"
      CHART_VERSION_FLAG="--version 62.7.0"
    fi
  fi

  if check_servicemonitor_crd || [[ "$OPERATOR_EXISTS" == "true" ]]; then
    if [[ "$OPERATOR_EXISTS" == "true" ]]; then
      log_info "🔄 Existing Prometheus operator manages CRDs - installing without CRDs to avoid conflicts"
    else
      log_info "🔄 ServiceMonitor CRDs already exist - installing without CRDs to avoid conflicts"
    fi
    CRD_INSTALL_FLAG="--skip-crds"
  else
    log_info "🆕 Installing Prometheus stack with CRDs"
    CRD_INSTALL_FLAG=""
  fi

  local DISABLE_NODE_EXPORTER=""
  if check_existing_node_exporter; then
    DISABLE_NODE_EXPORTER="nodeExporter:\n  enabled: false"
  fi

  log_info "🚀 Installing Prometheus stack in namespace ${MONITORING_NAMESPACE}..."

  # Determine protocol and port for Grafana datasource based on TLS setting
  if [[ "$ENABLE_TLS" == "true" ]]; then
    PROMETHEUS_PROTOCOL="https"
    PROMETHEUS_PORT="9090"
    WEB_TLS_CONFIG="    web:
      tlsConfig:
        cert:
          secret:
            name: prometheus-web-tls
            key: tls.crt
        keySecret:
          name: prometheus-web-tls
          key: tls.key"
  else
    PROMETHEUS_PROTOCOL="http"
    PROMETHEUS_PORT="9090"
    WEB_TLS_CONFIG=""
  fi

  if [[ "$CENTRAL_MODE" == "true" ]]; then
    cat <<EOF > /tmp/prometheus-values.yaml
grafana:
  adminPassword: admin
  service:
    type: ClusterIP
    sessionAffinity: ""
  sidecar:
    dashboards:
      enabled: true
      searchNamespace: ALL
    datasources:
      defaultDatasourceEnabled: false
  datasources:
    datasources.yaml:
      apiVersion: 1
      datasources:
      - name: Prometheus
        type: prometheus
        url: ${PROMETHEUS_PROTOCOL}://${RELEASE_NAME}-kube-prometheus-stack-prometheus.${MONITORING_NAMESPACE}.svc.cluster.local:${PROMETHEUS_PORT}
        access: proxy
        isDefault: true
$(if [[ "$ENABLE_TLS" == "true" ]]; then echo "        jsonData:
          tlsSkipVerify: true"; fi)
prometheus:
  service:
    type: ClusterIP
    sessionAffinity: ""
  prometheusSpec:
$(if [[ -n "$PROMETHEUS_IMAGE_CONFIG" ]]; then echo -e "$PROMETHEUS_IMAGE_CONFIG"; fi)
$(if [[ -n "$WEB_TLS_CONFIG" ]]; then echo -e "$WEB_TLS_CONFIG"; fi)
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector: {}
    serviceMonitorNamespaceSelector: {}
    podMonitorSelectorNilUsesHelmValues: false
    podMonitorSelector: {}
    podMonitorNamespaceSelector: {}
    maximumStartupDurationSeconds: 300
    resources:
      limits:
        memory: 8Gi
        cpu: 4000m
      requests:
        memory: 4Gi
        cpu: 1000m
$(if [[ -n "$DISABLE_PROMETHEUS_OPERATOR" ]]; then echo -e "$DISABLE_PROMETHEUS_OPERATOR"; fi)
$(if [[ -n "$DISABLE_NODE_EXPORTER" ]]; then echo -e "$DISABLE_NODE_EXPORTER"; fi)
EOF
  else
    cat <<EOF > /tmp/prometheus-values.yaml
grafana:
  adminPassword: admin
  service:
    type: ClusterIP
    sessionAffinity: ""
  sidecar:
    dashboards:
      enabled: true
      searchNamespace: ${MONITORING_NAMESPACE}
      label: grafana_dashboard
      folderAnnotation: grafana_folder
    datasources:
      defaultDatasourceEnabled: false
  datasources:
    datasources.yaml:
      apiVersion: 1
      datasources:
      - name: Prometheus
        type: prometheus
        url: ${PROMETHEUS_PROTOCOL}://${RELEASE_NAME}-kube-prometheus-stack-prometheus.${MONITORING_NAMESPACE}.svc.cluster.local:${PROMETHEUS_PORT}
        access: proxy
        isDefault: true
$(if [[ "$ENABLE_TLS" == "true" ]]; then echo "        jsonData:
          tlsSkipVerify: true"; fi)
prometheus:
  service:
    type: ClusterIP
    sessionAffinity: ""
  prometheusSpec:
$(if [[ -n "$PROMETHEUS_IMAGE_CONFIG" ]]; then echo -e "$PROMETHEUS_IMAGE_CONFIG"; fi)
$(if [[ -n "$WEB_TLS_CONFIG" ]]; then echo -e "$WEB_TLS_CONFIG"; fi)
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
    resources:
      limits:
        memory: 4Gi
        cpu: 2000m
      requests:
        memory: 2Gi
        cpu: 500m
$(if [[ -n "$DISABLE_PROMETHEUS_OPERATOR" ]]; then echo -e "$DISABLE_PROMETHEUS_OPERATOR"; fi)
$(if [[ -n "$DISABLE_NODE_EXPORTER" ]]; then echo -e "$DISABLE_NODE_EXPORTER"; fi)
EOF
  fi

  if [ "${CRD_INSTALL_FLAG:-}" != "--skip-crds" ]; then
    "$HCMD" show crds prometheus-community/kube-prometheus-stack ${CHART_VERSION_FLAG} \
    | "$KCMD" apply --server-side --validate=false -f -
  fi

  $HCMD install "${RELEASE_NAME}" prometheus-community/kube-prometheus-stack \
    --namespace "${MONITORING_NAMESPACE}" \
    ${DEBUG} \
    ${CHART_VERSION_FLAG} \
    --skip-crds \
    -f /tmp/prometheus-values.yaml

  rm -f /tmp/prometheus-values.yaml

  log_info "⏳ Waiting for Prometheus and Grafana to be ready..."

  # Wait for Prometheus custom resource to be available
  # The Prometheus CR is created by Helm and managed by the Prometheus operator
  log_info "⏳ Waiting for Prometheus instance to be available..."
  $KCMD wait --for=condition=Available prometheus "${RELEASE_NAME}-kube-prometheus-stack-prometheus" -n "${MONITORING_NAMESPACE}" --timeout=300s || log_info "⚠️  Prometheus instance did not become available within timeout"

  # Wait for Grafana deployment to be available
  log_info "⏳ Waiting for Grafana deployment to be available..."
  $KCMD wait --for=condition=Available deployment -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=grafana" -n "${MONITORING_NAMESPACE}" --timeout=300s || log_info "⚠️  Grafana deployment did not become available within timeout"

  # Use the known service name from the Helm chart
  PROMETHEUS_SVC="${RELEASE_NAME}-kube-prometheus-stack-prometheus"

  log_success "🚀 Prometheus and Grafana installed."

  log_info "📊 Access Information:"
  if [[ "$ENABLE_TLS" == "true" ]]; then
    log_info "   Prometheus (HTTPS): kubectl port-forward -n ${MONITORING_NAMESPACE} svc/${PROMETHEUS_SVC} 9090:9090"
    log_info "   Access via: https://localhost:9090"
    log_info "   🔐 TLS is enabled - clients must use HTTPS and trust the CA certificate"
    log_info "   Extract CA cert: kubectl get configmap prometheus-web-tls-ca -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}' > prometheus-ca.crt"
  else
    log_info "   Prometheus (HTTP): kubectl port-forward -n ${MONITORING_NAMESPACE} svc/${PROMETHEUS_SVC} 9090:9090"
    log_info "   Access via: http://localhost:9090"
  fi
  log_info "   Grafana: kubectl port-forward -n ${MONITORING_NAMESPACE} svc/${RELEASE_NAME}-grafana 3000:80"
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
    log_info "🔍 OpenShift detected - this script does not support OpenShift."
    check_openshift_monitoring
    log_info "ℹ️ Use OpenShift's built-in user workload monitoring instead of this script."
    log_info "ℹ️ See: https://docs.openshift.com/container-platform/latest/monitoring/enabling-monitoring-for-user-defined-projects.html"
    exit 0
  fi

  log_info "🔍 Checking for existing ServiceMonitor CRD..."
  if check_servicemonitor_crd; then
    log_info "✅ ServiceMonitor CRD found. Installing namespace-scoped Prometheus stack..."
  else
    log_info "⚠️ ServiceMonitor CRD not found. Installing Prometheus stack with CRDs..."
  fi
  install_prometheus_grafana

  # Load llm-d dashboards
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "${SCRIPT_DIR}/load-llm-d-dashboards.sh" ]; then
    log_info "📊 Loading llm-d dashboards..."
    "${SCRIPT_DIR}/load-llm-d-dashboards.sh" "${MONITORING_NAMESPACE}"
  fi

  log_info ""
  log_info "🔍 For distributed tracing (Jaeger), see: ./install-jaeger.sh"
  log_success "🎉 Prometheus and Grafana installation complete."
}

uninstall() {
  log_info "🗑️ Uninstalling Prometheus and Grafana stack..."

  # Must use 'llmd' not 'llm-d' to avoid Kubernetes 63-char service name limit
  RELEASE_NAME="llmd"

  if $HCMD list -n "${MONITORING_NAMESPACE}" | grep -q "${RELEASE_NAME}" 2>/dev/null; then
    log_info "🗑️ Uninstalling Prometheus helm release '${RELEASE_NAME}'..."
    $HCMD uninstall "${RELEASE_NAME}" --namespace "${MONITORING_NAMESPACE}" || true
  fi

  log_info "🗑️ Deleting monitoring namespace..."
  $KCMD delete namespace "${MONITORING_NAMESPACE}" --ignore-not-found || true

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
