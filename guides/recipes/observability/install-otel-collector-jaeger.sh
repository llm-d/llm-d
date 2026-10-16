#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
NAMESPACE=""
ACTION="install"
GKE_CLOUD_TRACE="false"

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy or remove an OpenTelemetry Collector and tracing backend (Jaeger or
Google Cloud Trace on GKE) for llm-d distributed tracing. Components in the
same namespace export traces to http://otel-collector:4317.

If --gke is set, the collector is deployed with the Google Cloud Trace
exporter (no Jaeger Deployment is created). Otherwise, Jaeger is deployed and,
if the OpenTelemetry Operator CRD is detected, the collector is deployed as an
OpenTelemetryCollector CR (or a standalone Deployment when the operator is not
installed).

Options:
  -n, --namespace NAME   Target namespace (required)
  --gke                  Export traces to Google Cloud Trace instead of Jaeger
  -u, --uninstall        Remove OTel Collector and Jaeger
  -h, --help             Show this help and exit

Examples:
  $(basename "$0") -n my-namespace          # Install OTel Collector + Jaeger
  $(basename "$0") --gke -n my-namespace    # Install OTel Collector for Google Cloud Trace
  $(basename "$0") -u -n my-namespace       # Uninstall from 'my-namespace'
EOF
}

# ANSI colour helpers
COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_RED=$'\e[31m'
COLOR_BLUE=$'\e[34m'

log_info()    { echo "${COLOR_BLUE}[INFO]  $*${COLOR_RESET}"; }
log_success() { echo "${COLOR_GREEN}[OK]    $*${COLOR_RESET}"; }
log_error()   { echo "${COLOR_RED}[ERROR] $*${COLOR_RESET}" >&2; }
fail()        { echo "${COLOR_RED}[ERROR] $*${COLOR_RESET}" >&2; exit 1; }

### ARG PARSING ###
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace) NAMESPACE="$2"; shift 2 ;;
      --gke)          GKE_CLOUD_TRACE="true"; shift ;;
      -u|--uninstall) ACTION="uninstall"; shift ;;
      -h|--help)      print_help; exit 0 ;;
      *)              fail "Unknown option: $1" ;;
    esac
  done
}

require_namespace() {
  if [[ -z "$NAMESPACE" ]]; then
    fail "Namespace is required. Use -n <namespace> to specify the target namespace."
  fi
}

### DETECTION ###
otel_operator_available() {
  kubectl get crd opentelemetrycollectors.opentelemetry.io &>/dev/null
}

### ACTIONS ###
install() {
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  TRACING_DIR="${SCRIPT_DIR}/tracing"

  # Create namespace if needed
  if ! kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    log_info "Creating namespace '${NAMESPACE}'..."
    kubectl create namespace "${NAMESPACE}"
  fi

  if [[ "$GKE_CLOUD_TRACE" == "true" ]]; then
    GKE_MANIFEST="${TRACING_DIR}/otel-collector-gke.yaml"
    if [[ ! -f "$GKE_MANIFEST" ]]; then
      fail "GKE OTel Collector manifest not found at: ${GKE_MANIFEST}"
    fi
    log_info "Deploying OTel Collector (Google Cloud Trace exporter) into namespace '${NAMESPACE}'..."
    kubectl apply -n "${NAMESPACE}" -f "${GKE_MANIFEST}"
    log_success "OTel Collector (Google Cloud Trace) deployed successfully."
    echo ""
    log_info "Components should export OTLP traces to:"
    echo "  http://otel-collector.${NAMESPACE}.svc.cluster.local:4317"
    echo "  (or simply http://otel-collector:4317 from the same namespace)"
    echo ""
    log_info "View traces in Google Cloud Trace Explorer:"
    echo "  https://console.cloud.google.com/traces/explorer"
    return
  fi

  log_info "Deploying OTel Collector + Jaeger into namespace '${NAMESPACE}'..."

  JAEGER_MANIFEST="${TRACING_DIR}/jaeger-all-in-one.yaml"
  if [[ ! -f "$JAEGER_MANIFEST" ]]; then
    fail "Jaeger manifest not found at: ${JAEGER_MANIFEST}"
  fi

  # Deploy Jaeger
  log_info "Deploying Jaeger all-in-one..."
  kubectl apply -n "${NAMESPACE}" -f "${JAEGER_MANIFEST}"

  # Deploy OTel Collector (operator CR or standalone)
  STANDALONE_MANIFEST="${TRACING_DIR}/otel-collector.yaml"
  if otel_operator_available; then
    COLLECTOR_MANIFEST="${TRACING_DIR}/otel-collector-operator.yaml"
    if [[ -f "$COLLECTOR_MANIFEST" ]]; then
      log_info "OpenTelemetry Operator detected -- deploying collector as OpenTelemetryCollector CR..."
      kubectl apply -n "${NAMESPACE}" -f "${COLLECTOR_MANIFEST}"
    elif [[ -f "$STANDALONE_MANIFEST" ]]; then
      log_info "OpenTelemetry Operator detected but operator manifest not found -- falling back to standalone collector..."
      kubectl apply -n "${NAMESPACE}" -f "${STANDALONE_MANIFEST}"
    else
      fail "No OTel Collector manifest found in: ${TRACING_DIR}"
    fi
  else
    if [[ ! -f "$STANDALONE_MANIFEST" ]]; then
      fail "OTel Collector standalone manifest not found at: ${STANDALONE_MANIFEST}"
    fi
    log_info "OpenTelemetry Operator not found -- deploying standalone collector..."
    kubectl apply -n "${NAMESPACE}" -f "${STANDALONE_MANIFEST}"
  fi

  log_success "OTel Collector + Jaeger deployed successfully."
  echo ""
  log_info "Access the Jaeger UI with:"
  echo "  kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686"
  echo "  Then open http://localhost:16686"
  echo ""
  log_info "Components should export OTLP traces to:"
  echo "  http://otel-collector.${NAMESPACE}.svc.cluster.local:4317"
  echo "  (or simply http://otel-collector:4317 from the same namespace)"
  echo ""
  log_info "To link Grafana's latency exemplars to these traces, re-run install-prometheus-grafana.sh."
}

uninstall() {
  log_info "Removing OTel Collector + Jaeger from namespace '${NAMESPACE}'..."

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  TRACING_DIR="${SCRIPT_DIR}/tracing"

  # Remove OTel Collector (try all variants)
  if otel_operator_available; then
    OPERATOR_MANIFEST="${TRACING_DIR}/otel-collector-operator.yaml"
    if [[ -f "$OPERATOR_MANIFEST" ]]; then
      kubectl delete -n "${NAMESPACE}" -f "${OPERATOR_MANIFEST}" --ignore-not-found
    fi
  fi

  for manifest in "${TRACING_DIR}/otel-collector.yaml" "${TRACING_DIR}/otel-collector-gke.yaml"; do
    if [[ -f "$manifest" ]]; then
      kubectl delete -n "${NAMESPACE}" -f "$manifest" --ignore-not-found
    fi
  done

  # Remove Jaeger
  JAEGER_MANIFEST="${TRACING_DIR}/jaeger-all-in-one.yaml"
  if [[ -f "$JAEGER_MANIFEST" ]]; then
    kubectl delete -n "${NAMESPACE}" -f "${JAEGER_MANIFEST}" --ignore-not-found
  else
    kubectl delete deployment jaeger -n "${NAMESPACE}" --ignore-not-found
    kubectl delete service jaeger-collector -n "${NAMESPACE}" --ignore-not-found
  fi

  log_success "OTel Collector + Jaeger removed from namespace '${NAMESPACE}'."
}

### MAIN ###
main() {
  parse_args "$@"

  command -v kubectl &>/dev/null || fail "kubectl is required but not found in PATH"

  require_namespace

  if [[ "$ACTION" == "install" ]]; then
    install
  elif [[ "$ACTION" == "uninstall" ]]; then
    uninstall
  fi
}

main "$@"
