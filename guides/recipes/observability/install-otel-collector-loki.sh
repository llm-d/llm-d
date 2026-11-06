#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
WORKLOAD_NAMESPACES=""
LOGGING_NAMESPACE="${LOGGING_NAMESPACE:-llm-d-logging}"
OTLP_ENDPOINT=""
ACTION="install"
# Set on a logging namespace this script creates, so uninstall only deletes a
# namespace it owns.
CREATED_BY="install-otel-collector-loki"

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy or remove a log pipeline that makes llm-d logs queryable by trace_id.
An OpenTelemetry Collector DaemonSet reads the pod logs of the given workload
namespaces on every node and sends them over OTLP to a single-binary Loki.

The agent and Loki run in their own namespace (llm-d-logging by default),
labeled for the privileged Pod Security level, because the agent has to run as
root to read /var/log/pods from the node. Running the script again with another
-n adds that namespace to the ones already collected.

OpenShift and GKE Autopilot are not supported, see
docs/operations/observability/logging.md for those platforms.

Options:
  -n, --namespace NAME          Workload namespace to collect logs from (required to install;
                                repeat it or pass a comma-separated list for several)
  -l, --logging-namespace NAME  Namespace for the agent and Loki (default: llm-d-logging)
  --otlp-endpoint URL           Send logs to this OTLP/HTTP endpoint instead of deploying Loki
  -u, --uninstall               Remove the agent and Loki
  -h, --help                    Show this help and exit

Environment Variables:
  LOGGING_NAMESPACE             Same as --logging-namespace

Examples:
  $(basename "$0") -n my-namespace       # Collect my-namespace into a new Loki
  $(basename "$0") -n other-namespace    # Also collect other-namespace
  $(basename "$0") -n my-namespace --otlp-endpoint http://loki.loki.svc.cluster.local:3100/otlp
  $(basename "$0") -u                    # Remove the agent and Loki
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
      -n|--namespace)         WORKLOAD_NAMESPACES="${WORKLOAD_NAMESPACES:+${WORKLOAD_NAMESPACES},}$2"; shift 2 ;;
      -l|--logging-namespace) LOGGING_NAMESPACE="$2"; shift 2 ;;
      --otlp-endpoint)        OTLP_ENDPOINT="$2"; shift 2 ;;
      -u|--uninstall)         ACTION="uninstall"; shift ;;
      -h|--help)              print_help; exit 0 ;;
      *)                      fail "Unknown option: $1" ;;
    esac
  done
}

### HELPERS ###
# merge_namespaces prints the union of comma-separated namespace lists, sorted
# and without duplicates, as one comma-separated list.
merge_namespaces() {
  printf '%s\n' "$@" | tr ',' '\n' | sed '/^$/d' | sort -u | paste -sd, -
}

# The agent's filelog include pattern is built from these names, so only allow
# what Kubernetes allows in a namespace name.
valid_namespace() {
  [[ "$1" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]
}

# collected_namespaces prints the namespaces an already deployed agent collects,
# so a re-run adds to them instead of replacing them.
collected_namespaces() {
  kubectl get daemonset otel-logs-agent -n "${LOGGING_NAMESPACE}" \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="LOG_NAMESPACES")].value}' 2>/dev/null || true
}

### DETECTION ###
is_openshift() {
  kubectl get clusterversion &>/dev/null
}

is_autopilot() {
  kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null | grep -q '^gk3-'
}

### ACTIONS ###
# ensure_logging_namespace creates the logging namespace if needed and makes sure
# Pod Security admits the agent. Baseline and restricted both forbid hostPath
# volumes, and a rejected DaemonSet creates no pods at all, so stop here rather
# than leave an agent that never runs.
ensure_logging_namespace() {
  if ! kubectl get namespace "${LOGGING_NAMESPACE}" &>/dev/null; then
    log_info "Creating namespace '${LOGGING_NAMESPACE}'..."
    kubectl create namespace "${LOGGING_NAMESPACE}"
    kubectl annotate namespace "${LOGGING_NAMESPACE}" "llm-d.ai/created-by=${CREATED_BY}"
  fi

  local level
  level="$(kubectl get namespace "${LOGGING_NAMESPACE}" \
    -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null || true)"
  case "$level" in
    privileged) ;;
    "")
      kubectl label namespace "${LOGGING_NAMESPACE}" pod-security.kubernetes.io/enforce=privileged
      ;;
    *)
      fail "Namespace '${LOGGING_NAMESPACE}' enforces the '${level}' Pod Security level, which does not allow the hostPath volumes the log agent needs. Use a dedicated namespace with -l, or label this one pod-security.kubernetes.io/enforce=privileged."
      ;;
  esac
}

wait_until_ready() {
  if [[ -z "$OTLP_ENDPOINT" ]]; then
    kubectl rollout status deployment/loki -n "${LOGGING_NAMESPACE}" --timeout=180s \
      || fail "Loki did not become ready. Check: kubectl describe pod -n ${LOGGING_NAMESPACE} -l app=loki"
  fi

  if ! kubectl rollout status daemonset/otel-logs-agent -n "${LOGGING_NAMESPACE}" --timeout=180s; then
    log_error "The log agent is not running on every node. Admission rejections, such as"
    log_error "Pod Security, show up in the DaemonSet events:"
    kubectl describe daemonset otel-logs-agent -n "${LOGGING_NAMESPACE}" | sed -n '/^Events:/,$p' >&2 || true
    exit 1
  fi
}

install() {
  [[ -n "$WORKLOAD_NAMESPACES" ]] \
    || fail "A workload namespace is required. Use -n <namespace> to pick the namespace whose logs to collect."

  local namespace
  for namespace in $(tr ',' ' ' <<<"$WORKLOAD_NAMESPACES"); do
    valid_namespace "$namespace" || fail "Invalid namespace name: '${namespace}'"
    if ! kubectl get namespace "$namespace" &>/dev/null; then
      log_info "Namespace '${namespace}' does not exist yet. Its logs are collected once it does."
    fi
  done

  if is_openshift; then
    fail "OpenShift is not supported by this recipe. Collect pod logs with OpenShift Logging (ClusterLogForwarder) instead, see docs/operations/observability/logging.md."
  fi
  if is_autopilot; then
    fail "GKE Autopilot does not allow the hostPath volumes the log agent needs. Pod logs already go to Cloud Logging there, see docs/operations/observability/logging.md."
  fi

  ensure_logging_namespace

  local endpoint="$OTLP_ENDPOINT"
  if [[ -z "$endpoint" ]]; then
    log_info "Deploying Loki into namespace '${LOGGING_NAMESPACE}'..."
    kubectl apply -n "${LOGGING_NAMESPACE}" -f "${LOGGING_DIR}/loki.yaml"
    endpoint="http://loki:3100/otlp"
  fi

  # Read before applying the manifest, which carries no namespaces of its own.
  local namespaces
  namespaces="$(merge_namespaces "$(collected_namespaces)" "$WORKLOAD_NAMESPACES")"

  log_info "Deploying the log agent DaemonSet, collecting namespaces: ${namespaces}"
  kubectl apply -n "${LOGGING_NAMESPACE}" -f "${LOGGING_DIR}/otel-collector-logs.yaml"
  kubectl set env daemonset/otel-logs-agent -n "${LOGGING_NAMESPACE}" -c otel-collector \
    "LOG_NAMESPACES=${namespaces}" "LOKI_OTLP_ENDPOINT=${endpoint}"

  wait_until_ready

  log_success "Log pipeline deployed. Logs from ${namespaces} are sent to ${endpoint}"
  if [[ -z "$OTLP_ENDPOINT" ]]; then
    echo ""
    log_info "Query the logs of one request by its trace_id through the Loki API:"
    echo "  kubectl port-forward -n ${LOGGING_NAMESPACE} svc/loki 3100:3100"
    echo "  curl -sG http://localhost:3100/loki/api/v1/query_range \\"
    echo "    --data-urlencode 'query={k8s_namespace_name=\"<namespace>\"} | trace_id=\"<trace-id>\"'"
    echo ""
    log_info "To browse them in Grafana, add a Loki data source with the URL:"
    echo "  http://loki.${LOGGING_NAMESPACE}.svc.cluster.local:3100"
  fi
}

uninstall() {
  log_info "Removing the log agent and Loki from namespace '${LOGGING_NAMESPACE}'..."

  kubectl delete -n "${LOGGING_NAMESPACE}" -f "${LOGGING_DIR}/otel-collector-logs.yaml" --ignore-not-found
  kubectl delete -n "${LOGGING_NAMESPACE}" -f "${LOGGING_DIR}/loki.yaml" --ignore-not-found

  local owner
  owner="$(kubectl get namespace "${LOGGING_NAMESPACE}" \
    -o jsonpath='{.metadata.annotations.llm-d\.ai/created-by}' 2>/dev/null || true)"
  if [[ "$owner" == "$CREATED_BY" ]]; then
    kubectl delete namespace "${LOGGING_NAMESPACE}" --ignore-not-found
  fi

  log_success "Log agent and Loki removed from namespace '${LOGGING_NAMESPACE}'."
  log_info "Read offsets stay on each node under /var/lib/otelcol-llm-d-logs/${LOGGING_NAMESPACE}."
}

### MAIN ###
main() {
  parse_args "$@"

  command -v kubectl &>/dev/null || fail "kubectl is required but not found in PATH"

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  LOGGING_DIR="${SCRIPT_DIR}/logging"

  if [[ "$ACTION" == "install" ]]; then
    install
  elif [[ "$ACTION" == "uninstall" ]]; then
    uninstall
  fi
}

main "$@"
