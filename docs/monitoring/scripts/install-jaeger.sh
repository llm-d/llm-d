#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
NAMESPACE="observability"
ACTION="install"

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy or remove Jaeger all-in-one for llm-d distributed tracing.

Options:
  -n, --namespace NAME   Target namespace (default: observability)
  -u, --uninstall        Remove Jaeger deployment and service
  -h, --help             Show this help and exit

Examples:
  $(basename "$0")                    # Install Jaeger in 'observability' namespace
  $(basename "$0") -n tracing         # Install in 'tracing' namespace
  $(basename "$0") -u                 # Uninstall from default namespace
  $(basename "$0") -u -n tracing      # Uninstall from 'tracing' namespace
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
fail()        { log_error "$*"; exit 1; }

### ARG PARSING ###
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace) NAMESPACE="$2"; shift 2 ;;
      -u|--uninstall) ACTION="uninstall"; shift ;;
      -h|--help)      print_help; exit 0 ;;
      *)              fail "Unknown option: $1" ;;
    esac
  done
}

### ACTIONS ###
install_jaeger() {
  log_info "Deploying Jaeger all-in-one into namespace '${NAMESPACE}'..."

  # Locate the manifest relative to this script
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  MANIFEST="${SCRIPT_DIR}/../tracing/jaeger-all-in-one.yaml"

  if [[ ! -f "$MANIFEST" ]]; then
    fail "Jaeger manifest not found at: ${MANIFEST}"
  fi

  if ! kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    log_info "Creating namespace '${NAMESPACE}'..."
    kubectl create namespace "${NAMESPACE}"
  fi

  kubectl apply -n "${NAMESPACE}" -f "${MANIFEST}"

  log_success "Jaeger deployed successfully."
  echo ""
  log_info "Access the Jaeger UI with:"
  echo "  kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686"
  echo "  Then open http://localhost:16686"
  echo ""
  log_info "Components should export OTLP traces to:"
  echo "  http://jaeger-collector.${NAMESPACE}.svc.cluster.local:4317"
}

uninstall_jaeger() {
  log_info "Removing Jaeger from namespace '${NAMESPACE}'..."

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  MANIFEST="${SCRIPT_DIR}/../tracing/jaeger-all-in-one.yaml"

  if [[ -f "$MANIFEST" ]]; then
    kubectl delete -n "${NAMESPACE}" -f "${MANIFEST}" --ignore-not-found
  else
    # Fallback: delete by label
    kubectl delete deployment jaeger -n "${NAMESPACE}" --ignore-not-found
    kubectl delete service jaeger-collector -n "${NAMESPACE}" --ignore-not-found
  fi

  log_success "Jaeger removed from namespace '${NAMESPACE}'."
}

### MAIN ###
main() {
  parse_args "$@"

  command -v kubectl &>/dev/null || fail "kubectl is required but not found in PATH"

  if [[ "$ACTION" == "install" ]]; then
    install_jaeger
  elif [[ "$ACTION" == "uninstall" ]]; then
    uninstall_jaeger
  fi
}

main "$@"
