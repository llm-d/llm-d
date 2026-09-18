#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
OPENCOST_NAMESPACE="llm-d-monitoring"
PROMETHEUS_ENDPOINT=""
PROMETHEUS_RELEASE_NAME="llmd"
INSTALL_PROMETHEUS=false
PRICING_CONFIG_FILE=""
CLUSTER_ID="llm-d"
YES=false
ACTION="install"
KUBERNETES_CONTEXT=""

# Temporary files — created by mktemp in setup_env and cleaned up on EXIT.
OPENCOST_PRICING_JSON=""
OPENCOST_VALUES_YAML=""

# Fork image override (set to non-empty to deploy a custom OpenCost image instead of the upstream release)
OPENCOST_IMAGE_REGISTRY=""
OPENCOST_IMAGE_REPO=""
OPENCOST_IMAGE_TAG=""

# Helm release name for OpenCost — derived from namespace after parse_args so it is
# unique per install and avoids ClusterRole name collisions when multiple OpenCost
# instances exist on the same cluster.
OPENCOST_RELEASE_NAME=""

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Install OpenCost with inference cost tracking for llm-d, or verify an existing
OpenCost installation is correctly wired to llm-d.

Options:
  -n, --namespace NAME          OpenCost namespace (default: llm-d-monitoring)
  --prometheus-endpoint URL     Override auto-detected Prometheus endpoint
  --prometheus-release-name     Prometheus Helm release name (default: llmd)
  --install-prometheus          Force-install kube-prometheus-stack alongside OpenCost
  --pricing-config FILE         Path to a pre-filled pricing JSON (skips interactive prompt)
  --cluster-id NAME             Cluster identifier label (default: llm-d)
  -g, --context FILE            Path to a specific kubeconfig context
  -y, --yes                     Accept default prices without interactive confirmation
  -u, --uninstall               Uninstall OpenCost
      --image REPO:TAG          Use a specific OpenCost image (e.g. "ghcr.io/opencost/opencost:develop-latest@sha256:...")
  -h, --help                    Show this help and exit

Examples:
  $(basename "$0")                                     # Auto-detect Prometheus and install
  $(basename "$0") --install-prometheus                # Install Prometheus + OpenCost together
  $(basename "$0") --pricing-config my-prices.json     # Install with pre-filled pricing
  $(basename "$0") -y                                  # Install accepting default GCP prices
  $(basename "$0") -u                                  # Uninstall OpenCost
EOF
}

COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_YELLOW=$'\e[33m'
COLOR_RED=$'\e[31m'
COLOR_BLUE=$'\e[34m'

log_info()    { echo "${COLOR_BLUE}ℹ️  $*${COLOR_RESET}"; }
log_success() { echo "${COLOR_GREEN}✅ $*${COLOR_RESET}"; }
log_warn()    { echo "${COLOR_YELLOW}⚠️  $*${COLOR_RESET}"; }
log_error()   { echo "${COLOR_RED}❌ $*${COLOR_RESET}" >&2; }
pass()        { echo "${COLOR_GREEN}  [PASS] $*${COLOR_RESET}"; }
fail()        { echo "${COLOR_RED}  [FAIL] $*${COLOR_RESET}" >&2; }

die() { log_error "$*"; exit 1; }

### UTILITIES ###
check_cmd() {
  command -v "$1" &>/dev/null || die "Required command not found: $1"
}

check_dependencies() {
  for cmd in helm kubectl jq; do
    check_cmd "$cmd"
  done
}

is_openshift() {
  $KCMD api-resources --api-group=security.openshift.io 2>/dev/null | grep -q "SecurityContextConstraints"
}

check_cluster_reachability() {
  if ! $KCMD cluster-info &>/dev/null; then
    die "kubectl cannot reach a running Kubernetes cluster. Ensure your kubeconfig is set up correctly."
  fi
  log_info "kubectl can reach the Kubernetes cluster."
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace)           OPENCOST_NAMESPACE="$2"; shift 2 ;;
      --prometheus-endpoint)    PROMETHEUS_ENDPOINT="$2"; shift 2 ;;
      --prometheus-release-name) PROMETHEUS_RELEASE_NAME="$2"; shift 2 ;;
      --install-prometheus)     INSTALL_PROMETHEUS=true; shift ;;
      --pricing-config)         PRICING_CONFIG_FILE="$2"; shift 2 ;;
      --cluster-id)             CLUSTER_ID="$2"; shift 2 ;;
      -g|--context)             KUBERNETES_CONTEXT="$2"; shift 2 ;;
      -y|--yes)                 YES=true; shift ;;
      -u|--uninstall)           ACTION="uninstall"; shift ;;
      --image)
        # Split REPO:TAG, then split registry/repository on the first slash-delimited component
        # e.g. ghcr.io/opencost/opencost:develop-latest@sha256:abc...
        #   -> registry=ghcr.io  repository=opencost/opencost  tag=develop-latest@sha256:abc...
        local _img="${2}"
        OPENCOST_IMAGE_TAG="${_img#*:}"
        local _repopath="${_img%%:*}"
        OPENCOST_IMAGE_REGISTRY="${_repopath%%/*}"
        OPENCOST_IMAGE_REPO="${_repopath#*/}"
        shift 2 ;;
      -h|--help)                print_help; exit 0 ;;
      *)                        die "Unknown option: $1" ;;
    esac
  done
}

setup_env() {
  if [[ -n "$KUBERNETES_CONTEXT" ]]; then
    [[ -f "$KUBERNETES_CONTEXT" ]] || die "Context file not found: $KUBERNETES_CONTEXT"
    KCMD="kubectl --kubeconfig $KUBERNETES_CONTEXT"
    HCMD="helm --kubeconfig $KUBERNETES_CONTEXT"
    OCMD="oc --kubeconfig $KUBERNETES_CONTEXT"
  else
    KCMD="kubectl"
    HCMD="helm"
    OCMD="oc"
  fi
  # Derive a unique Helm release name from the namespace so that cluster-scoped
  # resources (ClusterRole, ClusterRoleBinding) don't collide when multiple
  # OpenCost instances are installed on the same cluster.
  OPENCOST_RELEASE_NAME="opencost-${OPENCOST_NAMESPACE}"

  # Create temp files and register a trap to clean them up on any exit.
  OPENCOST_PRICING_JSON=$(mktemp)
  OPENCOST_VALUES_YAML=$(mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '${OPENCOST_PRICING_JSON}' '${OPENCOST_VALUES_YAML}'" EXIT
}

### OPENCOST DETECTION ###
detect_opencost() {
  log_info "Checking for existing OpenCost installation in namespace ${OPENCOST_NAMESPACE}..."
  OPENCOST_RELEASE=$($HCMD list -n "${OPENCOST_NAMESPACE}" -o json 2>/dev/null \
    | jq -r '.[] | select(.chart | startswith("opencost")) | "\(.namespace)/\(.name)/\(.app_version)"' \
    | head -1)

  if [[ -z "$OPENCOST_RELEASE" ]]; then
    log_info "OpenCost not found in namespace ${OPENCOST_NAMESPACE} — proceeding with install."
    OPENCOST_EXISTS=false
    return
  fi

  OPENCOST_EXISTS=true
  OPENCOST_NS=$(echo "$OPENCOST_RELEASE" | cut -d/ -f1)
  OPENCOST_RNAME=$(echo "$OPENCOST_RELEASE" | cut -d/ -f2)
  OPENCOST_VERSION=$(echo "$OPENCOST_RELEASE" | cut -d/ -f3)
  log_info "Found OpenCost release '$OPENCOST_RNAME' in namespace '$OPENCOST_NS' (version: $OPENCOST_VERSION)."
}


### PROMETHEUS DETECTION ###
detect_prometheus() {
  if [[ -n "$PROMETHEUS_ENDPOINT" ]]; then
    log_info "Using user-supplied Prometheus endpoint: $PROMETHEUS_ENDPOINT"
    return 0
  fi

  log_info "Detecting Prometheus..."

  # Check for Prometheus Operator CRD
  if $KCMD get crd prometheuses.monitoring.coreos.com &>/dev/null; then
    # Try the well-known llm-d release name first
    local svc_name="${PROMETHEUS_RELEASE_NAME}-kube-prometheus-stack-prometheus"
    local svc_ns
    svc_ns=$($KCMD get svc --all-namespaces -o jsonpath="{.items[?(@.metadata.name==\"${svc_name}\")].metadata.namespace}" 2>/dev/null | awk '{print $1}')

    if [[ -n "$svc_ns" ]]; then
      PROMETHEUS_ENDPOINT="http://${svc_name}.${svc_ns}.svc.cluster.local:9090"
      log_success "Found Prometheus: $PROMETHEUS_ENDPOINT"
      PROMETHEUS_EXISTS=true
      return 0
    fi

    # Fall back to label-based discovery
    local svc_json
    svc_json=$($KCMD get svc --all-namespaces -l "app=prometheus" -o json 2>/dev/null)
    local found_name found_ns
    found_name=$(echo "$svc_json" | jq -r '.items[0].metadata.name // empty')
    found_ns=$(echo "$svc_json" | jq -r '.items[0].metadata.namespace // empty')
    if [[ -n "$found_name" && -n "$found_ns" ]]; then
      PROMETHEUS_ENDPOINT="http://${found_name}.${found_ns}.svc.cluster.local:9090"
      log_success "Found Prometheus: $PROMETHEUS_ENDPOINT"
      PROMETHEUS_EXISTS=true
      return 0
    fi
  fi

  # Check for running Prometheus pods (non-operator installs)
  local pods
  pods=$($KCMD get pods --all-namespaces -l "app=prometheus" -o name 2>/dev/null | wc -l)
  if [[ $pods -gt 0 ]]; then
    log_warn "Found Prometheus pods but could not determine service endpoint."
    log_warn "Use --prometheus-endpoint to specify it explicitly."
    PROMETHEUS_EXISTS=true
    return 0
  fi

  PROMETHEUS_EXISTS=false
  return 1
}

prompt_prometheus_install() {
  if [[ "$INSTALL_PROMETHEUS" == "true" ]]; then
    PROMETHEUS_INSTALL_NS="$OPENCOST_NAMESPACE"
    return
  fi

  log_warn "No Prometheus found. OpenCost requires Prometheus to function."
  echo ""
  echo "  Options:"
  echo "    [1] Install kube-prometheus-stack in the same namespace as OpenCost (${OPENCOST_NAMESPACE})"
  echo "    [2] Install kube-prometheus-stack in a separate namespace"
  echo "    [q] Quit — install Prometheus separately, then re-run this script"
  echo ""
  read -rp "Choice [1/2/q]: " choice
  case "$choice" in
    1) PROMETHEUS_INSTALL_NS="$OPENCOST_NAMESPACE" ;;
    2)
      read -rp "Prometheus namespace [llm-d-monitoring]: " pns
      PROMETHEUS_INSTALL_NS="${pns:-llm-d-monitoring}"
      ;;
    [qQ])
      log_info "Re-run after installing Prometheus. Use --prometheus-endpoint to specify its address."
      exit 0
      ;;
    *) die "Invalid choice." ;;
  esac
}

install_prometheus() {
  log_info "Installing kube-prometheus-stack in namespace ${PROMETHEUS_INSTALL_NS}..."
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local prom_script="${script_dir}/../install-prometheus-grafana.sh"

  if [[ ! -f "$prom_script" ]]; then
    die "install-prometheus-grafana.sh not found at $prom_script"
  fi

  "$prom_script" -n "${PROMETHEUS_INSTALL_NS}"

  local svc_name="${PROMETHEUS_RELEASE_NAME}-kube-prometheus-stack-prometheus"
  PROMETHEUS_ENDPOINT="http://${svc_name}.${PROMETHEUS_INSTALL_NS}.svc.cluster.local:9090"
  log_success "Prometheus installed. Endpoint: $PROMETHEUS_ENDPOINT"
}

### LLM-D CONFIG CHECK ###

# Local port used for the temporary Prometheus port-forward during checks.
PROM_LOCAL_PORT=19090
PROM_PF_PID=""

start_prometheus_portforward() {
  local prom_svc prom_ns prom_port
  prom_svc=$(echo "$PROMETHEUS_ENDPOINT" | sed 's|http[s]*://||' | cut -d. -f1)
  prom_ns=$(echo "$PROMETHEUS_ENDPOINT" | sed 's|http[s]*://||' | cut -d. -f2)
  prom_port=$(echo "$PROMETHEUS_ENDPOINT" | grep -oE ':[0-9]+$' | tr -d ':')
  prom_port="${prom_port:-9090}"

  # Kill any leftover port-forward on this port from a previous run
  local stale
  stale=$(lsof -ti tcp:"${PROM_LOCAL_PORT}" 2>/dev/null || true)
  [[ -n "$stale" ]] && kill $stale 2>/dev/null || true

  # Check if the endpoint is already reachable (e.g. running inside the cluster)
  if curl -sf --max-time 3 "${PROMETHEUS_ENDPOINT}/api/v1/query?query=up" &>/dev/null; then
    return 0
  fi

  log_info "Starting temporary port-forward to Prometheus for config checks..."
  $KCMD port-forward -n "${prom_ns}" "svc/${prom_svc}" "${PROM_LOCAL_PORT}:${prom_port}" &
  PROM_PF_PID=$!
  # Wait for the port-forward to be ready
  local i=0
  while [[ $i -lt 15 ]]; do
    if curl -sf --max-time 2 "http://localhost:${PROM_LOCAL_PORT}/api/v1/query?query=up" &>/dev/null; then
      log_success "Port-forward to Prometheus established on localhost:${PROM_LOCAL_PORT}"
      return 0
    fi
    sleep 1
    (( i++ ))
  done
  log_warn "Could not establish port-forward to Prometheus — config checks may fail."
}

stop_prometheus_portforward() {
  if [[ -n "$PROM_PF_PID" ]]; then
    kill "$PROM_PF_PID" 2>/dev/null || true
    wait "$PROM_PF_PID" 2>/dev/null || true
    PROM_PF_PID=""
  fi
}

# Returns the first scalar value from a Prometheus instant query, or empty string on no data/error.
query_prometheus() {
  local query="$1"
  local endpoint
  if curl -sf --max-time 2 "http://localhost:${PROM_LOCAL_PORT}/api/v1/query?query=up" &>/dev/null; then
    endpoint="http://localhost:${PROM_LOCAL_PORT}"
  else
    endpoint="$PROMETHEUS_ENDPOINT"
  fi
  curl -sf --max-time 10 \
    "${endpoint}/api/v1/query" \
    --data-urlencode "query=${query}" \
    | jq -r '.data.result[0].value[1] // empty' 2>/dev/null || true
}

# Returns all label values for a given label from a Prometheus instant query.
query_prometheus_labels() {
  local query="$1" label="$2"
  local endpoint
  if curl -sf --max-time 2 "http://localhost:${PROM_LOCAL_PORT}/api/v1/query?query=up" &>/dev/null; then
    endpoint="http://localhost:${PROM_LOCAL_PORT}"
  else
    endpoint="$PROMETHEUS_ENDPOINT"
  fi
  curl -sf --max-time 10 \
    "${endpoint}/api/v1/query" \
    --data-urlencode "query=${query}" \
    | jq -r --arg l "$label" '.data.result[].metric[$l] // empty' 2>/dev/null | sort -u || true
}

check_llmd_config() {
  local errors=0
  log_info "Checking llm-d configuration for OpenCost inference cost compatibility..."
  echo ""

  # 1. kube-state-metrics exposes llm-d.ai/model in kube_pod_labels
  local ksm_result
  ksm_result=$(query_prometheus 'count(kube_pod_labels{label_llm_d_ai_model!=""})' 2>/dev/null || echo "")
  if [[ -n "$ksm_result" && "$ksm_result" != "0" ]]; then
    pass "kube-state-metrics exposes llm-d.ai/model in kube_pod_labels ($ksm_result pod(s))"
  else
    fail "kube-state-metrics does not expose llm-d.ai/model in kube_pod_labels"
    log_error "  Fix: add metricLabelsAllowlist to kube-prometheus-stack values and run: helm upgrade ${PROMETHEUS_RELEASE_NAME}"
    errors=$(( errors + 1 ))
  fi

  # 2. Shared infrastructure pods (router/EPP/gateway) carry llm-d.ai/inference-shared=true
  local serving_pods
  serving_pods=$($KCMD get pods --all-namespaces -l "llm-d.ai/inference-shared=true" \
    --no-headers 2>/dev/null | wc -l)
  if [[ "$serving_pods" -gt 0 ]]; then
    pass "Found $serving_pods shared infrastructure pod(s) with llm-d.ai/inference-shared=true"
  else
    log_warn "  [WARN] No pods found with llm-d.ai/inference-shared=true"
    log_warn "  This is expected if llm-d router/EPP/gateway are not yet deployed."
    log_warn "  Fix: ensure shared infrastructure pod templates carry the label llm-d.ai/inference-shared=true"
  fi

  # 3. vLLM token metrics present in Prometheus
  local token_result
  token_result=$(query_prometheus 'count(vllm:prompt_tokens_total)' 2>/dev/null || echo "")
  if [[ -n "$token_result" && "$token_result" != "0" ]]; then
    pass "vLLM token metrics (vllm:prompt_tokens_total) present in Prometheus ($token_result series)"
  else
    log_warn "  [WARN] No vllm:prompt_tokens_total metrics found in Prometheus"
    log_warn "  If Prometheus was just installed, wait ~2 minutes for the first scrape window and re-run."
    log_warn "  Fix: ensure PodMonitors are deployed and vLLM pods are running and being scraped"
  fi

  # 4. model_name in vLLM metrics matches llm-d.ai/model pod labels
  local metric_models pod_models mismatches
  metric_models=$(query_prometheus_labels 'group by(model_name)(vllm:prompt_tokens_total)' 'model_name' 2>/dev/null || echo "")
  pod_models=$($KCMD get pods --all-namespaces -l "llm-d.ai/inference-serving=true" \
    -o jsonpath='{.items[*].metadata.labels.llm-d\.ai/model}' 2>/dev/null \
    | tr ' ' '\n' | sort -u || echo "")

  if [[ -n "$metric_models" && -n "$pod_models" ]]; then
    mismatches=$(comm -23 <(echo "$metric_models") <(echo "$pod_models") || echo "")
    if [[ -z "$mismatches" ]]; then
      pass "vLLM metric model_name values match llm-d.ai/model pod labels"
    else
      fail "model_name mismatch between vLLM metrics and pod labels: $mismatches"
      log_error "  Fix: add --served-model-name=<short-name> to vllm serve args, matching the llm-d.ai/model pod label"
      errors=$(( errors + 1 ))
    fi
  else
    log_warn "  Could not compare model names (no metrics or no pods found)"
  fi

  # 5. INFERENCE_COST_ENABLED=true in OpenCost pod (only if OpenCost is already installed)
  if [[ "${OPENCOST_EXISTS:-false}" == "true" ]]; then
    local oc_pod oc_ns
    oc_pod=$($KCMD get pods --all-namespaces -l "app.kubernetes.io/name=opencost" \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    oc_ns=$($KCMD get pods --all-namespaces -l "app.kubernetes.io/name=opencost" \
      -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || echo "")
    if [[ -n "$oc_pod" ]]; then
      if $KCMD exec -n "$oc_ns" "$oc_pod" -- env 2>/dev/null | grep -q "INFERENCE_COST_ENABLED=true"; then
        pass "INFERENCE_COST_ENABLED=true in OpenCost pod"
      else
        fail "INFERENCE_COST_ENABLED is not set to true in the OpenCost pod"
        log_error "  Fix: helm upgrade opencost with INFERENCE_COST_ENABLED=true in exporter.extraEnv"
        errors=$(( errors + 1 ))
      fi
    fi
  fi

  echo ""
  if [[ $errors -eq 0 ]]; then
    log_success "All checks passed — llm-d is correctly configured for OpenCost inference cost tracking."
  else
    log_error "$errors check(s) failed. Resolve the issues above and re-run."
  fi
  return $errors
}

### PRICING ###
# Defaults sourced from opencost/configs/default.json
DEFAULT_CPU="0.031611"
DEFAULT_SPOT_CPU="0.006655"
DEFAULT_RAM="0.004237"
DEFAULT_SPOT_RAM="0.000892"
DEFAULT_GPU="0.95"
DEFAULT_STORAGE="0.00005479452"
DEFAULT_ZONE_EGRESS="0.01"
DEFAULT_REGION_EGRESS="0.01"
DEFAULT_INTERNET_EGRESS="0.12"

confirm_pricing() {
  if [[ -n "$PRICING_CONFIG_FILE" ]]; then
    [[ -f "$PRICING_CONFIG_FILE" ]] || die "Pricing config file not found: $PRICING_CONFIG_FILE"
    cp "$PRICING_CONFIG_FILE" "${OPENCOST_PRICING_JSON}"
    log_info "Using pricing config from $PRICING_CONFIG_FILE"
    return
  fi

  log_warn "OpenCost uses GCP us-central1 prices by default."
  log_warn "Update these values to match your actual infrastructure costs."
  echo ""
  printf "  %-30s %s\n"   "CPU (\$/core-hour):"       "$DEFAULT_CPU"
  printf "  %-30s %s\n"   "Spot CPU (\$/core-hour):"  "$DEFAULT_SPOT_CPU"
  printf "  %-30s %s\n"   "RAM (\$/GiB-hour):"        "$DEFAULT_RAM"
  printf "  %-30s %s\n"   "Spot RAM (\$/GiB-hour):"   "$DEFAULT_SPOT_RAM"
  printf "  %-30s %s  <-- most important for llm-d\n" "GPU (\$/GPU-hour):" "$DEFAULT_GPU"
  printf "  %-30s %s\n"   "Storage (\$/GiB-hour):"    "$DEFAULT_STORAGE"
  printf "  %-30s %s\n"   "Zone network egress:"      "$DEFAULT_ZONE_EGRESS"
  printf "  %-30s %s\n"   "Region network egress:"    "$DEFAULT_REGION_EGRESS"
  printf "  %-30s %s\n"   "Internet egress:"          "$DEFAULT_INTERNET_EGRESS"
  echo ""

  local CPU SPOT_CPU RAM SPOT_RAM GPU STORAGE ZONE_EGRESS REGION_EGRESS INTERNET_EGRESS

  if [[ "$YES" == "true" ]]; then
    log_warn "Proceeding with default (GCP) prices (--yes). Update later via --pricing-config."
    CPU=$DEFAULT_CPU; SPOT_CPU=$DEFAULT_SPOT_CPU
    RAM=$DEFAULT_RAM; SPOT_RAM=$DEFAULT_SPOT_RAM
    GPU=$DEFAULT_GPU; STORAGE=$DEFAULT_STORAGE
    ZONE_EGRESS=$DEFAULT_ZONE_EGRESS; REGION_EGRESS=$DEFAULT_REGION_EGRESS; INTERNET_EGRESS=$DEFAULT_INTERNET_EGRESS
  else
    read -rp "Accept defaults? [Enter/Y] accept  [e] edit fields  [q] quit: " choice
    case "${choice:-y}" in
      [eE])
        read -rp "  CPU    [$DEFAULT_CPU]: "              v; CPU="${v:-$DEFAULT_CPU}"
        read -rp "  Spot CPU [$DEFAULT_SPOT_CPU]: "       v; SPOT_CPU="${v:-$DEFAULT_SPOT_CPU}"
        read -rp "  RAM    [$DEFAULT_RAM]: "              v; RAM="${v:-$DEFAULT_RAM}"
        read -rp "  Spot RAM [$DEFAULT_SPOT_RAM]: "       v; SPOT_RAM="${v:-$DEFAULT_SPOT_RAM}"
        read -rp "  GPU    [$DEFAULT_GPU]: "              v; GPU="${v:-$DEFAULT_GPU}"
        read -rp "  Storage [$DEFAULT_STORAGE]: "         v; STORAGE="${v:-$DEFAULT_STORAGE}"
        read -rp "  Zone egress [$DEFAULT_ZONE_EGRESS]: " v; ZONE_EGRESS="${v:-$DEFAULT_ZONE_EGRESS}"
        read -rp "  Region egress [$DEFAULT_REGION_EGRESS]: " v; REGION_EGRESS="${v:-$DEFAULT_REGION_EGRESS}"
        read -rp "  Internet egress [$DEFAULT_INTERNET_EGRESS]: " v; INTERNET_EGRESS="${v:-$DEFAULT_INTERNET_EGRESS}"
        ;;
      [qQ])
        log_info "Aborted. Re-run with --pricing-config <file> to supply prices non-interactively."
        exit 0
        ;;
      *)
        CPU=$DEFAULT_CPU; SPOT_CPU=$DEFAULT_SPOT_CPU
        RAM=$DEFAULT_RAM; SPOT_RAM=$DEFAULT_SPOT_RAM
        GPU=$DEFAULT_GPU; STORAGE=$DEFAULT_STORAGE
        ZONE_EGRESS=$DEFAULT_ZONE_EGRESS; REGION_EGRESS=$DEFAULT_REGION_EGRESS; INTERNET_EGRESS=$DEFAULT_INTERNET_EGRESS
        ;;
    esac
  fi

  log_info "Prices: CPU=\$$CPU  RAM=\$$RAM  GPU=\$$GPU  Storage=\$$STORAGE"

  cat > "${OPENCOST_PRICING_JSON}" <<EOF
{
  "provider": "custom",
  "description": "llm-d on-prem pricing (configured during install)",
  "CPU": "$CPU",
  "spotCPU": "$SPOT_CPU",
  "RAM": "$RAM",
  "spotRAM": "$SPOT_RAM",
  "GPU": "$GPU",
  "storage": "$STORAGE",
  "zoneNetworkEgress": "$ZONE_EGRESS",
  "regionNetworkEgress": "$REGION_EGRESS",
  "internetNetworkEgress": "$INTERNET_EGRESS"
}
EOF
  log_info "Pricing config written to ${OPENCOST_PRICING_JSON}"
}

### OPENSHIFT SCC ###
apply_openshift_sccs() {
  local prom_release="$1"   # e.g. llm-d-test-prom
  local oc_release="$2"     # e.g. opencost-llm-d-cost-test
  local ns="$3"

  if ! command -v oc &>/dev/null; then
    log_warn "oc not found — cannot apply OpenShift SCCs automatically."
    log_warn "Apply them manually before pods will schedule:"
    log_warn "  oc adm policy add-scc-to-user privileged -z ${prom_release}-prometheus-server -n ${ns}"
    log_warn "  oc adm policy add-scc-to-user privileged -z ${prom_release}-kube-state-metrics -n ${ns}"
    log_warn "  oc adm policy add-scc-to-user privileged -z ${prom_release}-prometheus-node-exporter -n ${ns}"
    log_warn "  oc adm policy add-scc-to-user anyuid -z ${oc_release} -n ${ns}"
    return
  fi

  log_info "OpenShift detected — applying required SCCs in namespace ${ns}..."

  # prometheus-server and kube-state-metrics set seccompProfile=RuntimeDefault, which anyuid
  # forbids on some clusters — use privileged to allow any seccomp profile and any UID.
  $OCMD adm policy add-scc-to-user privileged \
    -z "${prom_release}-prometheus-server" -n "${ns}" \
    || log_warn "Failed to add privileged SCC to ${prom_release}-prometheus-server"
  $OCMD adm policy add-scc-to-user privileged \
    -z "${prom_release}-kube-state-metrics" -n "${ns}" \
    || log_warn "Failed to add privileged SCC to ${prom_release}-kube-state-metrics"
  $OCMD adm policy add-scc-to-user privileged \
    -z "${prom_release}-prometheus-node-exporter" -n "${ns}" \
    || log_warn "Failed to add privileged SCC to ${prom_release}-prometheus-node-exporter"
  $OCMD adm policy add-scc-to-user anyuid \
    -z "${oc_release}" -n "${ns}" \
    || log_warn "Failed to add anyuid SCC to ${oc_release}"

  log_info "Restarting workloads to pick up new SCCs..."
  $KCMD rollout restart \
    deployment/"${prom_release}-prometheus-server" \
    deployment/"${prom_release}-kube-state-metrics" \
    deployment/"${oc_release}" \
    -n "${ns}" 2>/dev/null || true
  $KCMD rollout restart \
    daemonset/"${prom_release}-prometheus-node-exporter" \
    -n "${ns}" 2>/dev/null || true

  log_success "OpenShift SCCs applied and workloads restarted."
}

### INSTALL ###
install_opencost() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  # Create namespace
  if ! $KCMD get namespace "${OPENCOST_NAMESPACE}" &>/dev/null; then
    log_info "Creating namespace ${OPENCOST_NAMESPACE}..."
    $KCMD create namespace "${OPENCOST_NAMESPACE}"
  fi

  # Deploy pricing ConfigMap with Helm adoption annotations so the opencost
  # Helm release can manage it without ownership conflicts.
  log_info "Deploying pricing ConfigMap..."
  $KCMD create configmap opencost-custom-pricing \
    --from-file=default.json="${OPENCOST_PRICING_JSON}" \
    --namespace "${OPENCOST_NAMESPACE}" \
    --dry-run=client -o yaml \
  | $KCMD annotate --local -f - \
      meta.helm.sh/release-name="${OPENCOST_RELEASE_NAME}" \
      meta.helm.sh/release-namespace="${OPENCOST_NAMESPACE}" \
      -o yaml \
  | $KCMD label --local -f - \
      app.kubernetes.io/managed-by=Helm \
      -o yaml \
  | $KCMD apply -f -

  # Deploy metrics-config ConfigMap with Helm adoption annotations.
  log_info "Deploying metrics-config ConfigMap..."
  $KCMD apply -n "${OPENCOST_NAMESPACE}" \
    -f "${script_dir}/manifests/metrics-config.yaml"
  $KCMD annotate configmap metrics-config -n "${OPENCOST_NAMESPACE}" \
    meta.helm.sh/release-name="${OPENCOST_RELEASE_NAME}" \
    meta.helm.sh/release-namespace="${OPENCOST_NAMESPACE}" \
    --overwrite
  $KCMD label configmap metrics-config -n "${OPENCOST_NAMESPACE}" \
    app.kubernetes.io/managed-by=Helm \
    --overwrite

  # Add Helm repo
  if ! $HCMD repo list 2>/dev/null | grep -q "https://opencost.github.io/opencost-helm-chart"; then
    log_info "Adding opencost Helm repo..."
    $HCMD repo add opencost-charts https://opencost.github.io/opencost-helm-chart
    $HCMD repo update
  fi

  # Build Prometheus service name/namespace/port from endpoint URL for Helm values
  # e.g. http://llmd-kube-prometheus-stack-prometheus.ns.svc.cluster.local:9090
  local prom_svc prom_ns prom_port
  prom_svc=$(echo "$PROMETHEUS_ENDPOINT" | sed 's|http[s]*://||' | cut -d. -f1)
  prom_ns=$(echo "$PROMETHEUS_ENDPOINT" | sed 's|http[s]*://||' | cut -d. -f2)
  prom_port=$(echo "$PROMETHEUS_ENDPOINT" | grep -oE ':[0-9]+$' | tr -d ':')
  prom_port="${prom_port:-9090}"

  # Generate merged values
  cat > "${OPENCOST_VALUES_YAML}" <<EOF
opencost:
  exporter:
    defaultClusterId: "${CLUSTER_ID}"
    extraEnv:
      INFERENCE_COST_ENABLED: "true"
      INFERENCE_MODEL_LABEL: "llm-d.ai/model"
      INFERENCE_SHARED_INFRA_LABEL: "llm-d.ai/inference-shared"
      INFERENCE_SHARED_INFRA_LABEL_VALUE: "true"
  prometheus:
    internal:
      enabled: true
      serviceName: "${prom_svc}"
      namespaceName: "${prom_ns}"
      port: ${prom_port}
  metrics:
    serviceMonitor:
      enabled: true
      scrapeInterval: 1m
      honorLabels: true
      additionalLabels:
        release: "${PROMETHEUS_RELEASE_NAME}"
  ui:
    enabled: true
  customPricing:
    enabled: true
    configmapName: opencost-custom-pricing
EOF

  log_info "Installing OpenCost in namespace ${OPENCOST_NAMESPACE}..."
  local image_sets=()
  if [[ -n "$OPENCOST_IMAGE_REPO" ]]; then
    log_info "OpenCost image: ${OPENCOST_IMAGE_REPO}:${OPENCOST_IMAGE_TAG}"
    image_sets=(
      --set "opencost.exporter.image.registry=${OPENCOST_IMAGE_REGISTRY}"
      --set "opencost.exporter.image.repository=${OPENCOST_IMAGE_REPO}"
      --set "opencost.exporter.image.tag=${OPENCOST_IMAGE_TAG}"
    )
  fi
  $HCMD install "${OPENCOST_RELEASE_NAME}" opencost-charts/opencost \
    --namespace "${OPENCOST_NAMESPACE}" \
    --create-namespace \
    -f "${OPENCOST_VALUES_YAML}" \
    "${image_sets[@]}"

  log_success "OpenCost installed."

  if is_openshift; then
    apply_openshift_sccs \
      "${PROMETHEUS_RELEASE_NAME}" \
      "${OPENCOST_RELEASE_NAME}" \
      "${OPENCOST_NAMESPACE}"
  fi
  echo ""
  log_info "Access the OpenCost UI:"
  log_info "  kubectl port-forward -n ${OPENCOST_NAMESPACE} svc/${OPENCOST_RELEASE_NAME} 9090:9090"
  log_info "  Open: http://localhost:9090"
  echo ""
  local prom_svc_name
  prom_svc_name=$(echo "$PROMETHEUS_ENDPOINT" | sed 's|http[s]*://||' | cut -d. -f1)
  log_info "Access the Prometheus UI:"
  log_info "  kubectl port-forward -n ${OPENCOST_NAMESPACE} svc/${prom_svc_name} 9092:80"
  log_info "  Open: http://localhost:9092"
  echo ""
  log_info "Query inference cost metrics (start a port-forward first):"
  log_info "  kubectl port-forward -n ${OPENCOST_NAMESPACE} svc/${OPENCOST_RELEASE_NAME} 9003:9003"
  echo ""
  log_info "  # Raw Prometheus gauges:"
  log_info "  curl -s http://localhost:9003/metrics | grep llm_"
  echo ""
  log_info "  # Cost time series for the last 5 hours, broken down by model:"
  log_info "  curl 'http://localhost:9003/inferenceCost/timeseries?window=5h&aggregate=model_name&accumulate=hour'"
}

wait_for_prometheus_scrape() {
  local timeout=120
  local interval=5
  local elapsed=0
  log_info "Waiting for Prometheus to complete its first scrape (up to ${timeout}s)..."
  while [[ $elapsed -lt $timeout ]]; do
    local result
    result=$(query_prometheus 'count(kube_state_metrics_build_info)' 2>/dev/null || echo "")
    if [[ -n "$result" && "$result" != "0" ]]; then
      log_success "Prometheus has scraped kube-state-metrics."
      return 0
    fi
    sleep "$interval"
    elapsed=$(( elapsed + interval ))
    log_info "  Still waiting... (${elapsed}s)"
  done
  log_warn "Timed out waiting for Prometheus scrape — running checks anyway."
}

### UNINSTALL ###
uninstall_opencost() {
  log_info "Uninstalling OpenCost..."
  if $HCMD list -n "${OPENCOST_NAMESPACE}" | grep -q "${OPENCOST_RELEASE_NAME}"; then
    $HCMD uninstall "${OPENCOST_RELEASE_NAME}" -n "${OPENCOST_NAMESPACE}"
    log_success "OpenCost uninstalled."
  else
    log_warn "OpenCost release '${OPENCOST_RELEASE_NAME}' not found in namespace ${OPENCOST_NAMESPACE}."
  fi

  if $KCMD get configmap opencost-custom-pricing -n "${OPENCOST_NAMESPACE}" &>/dev/null; then
    $KCMD delete configmap opencost-custom-pricing -n "${OPENCOST_NAMESPACE}"
  fi
  if $KCMD get configmap metrics-config -n "${OPENCOST_NAMESPACE}" &>/dev/null; then
    $KCMD delete configmap metrics-config -n "${OPENCOST_NAMESPACE}"
  fi
}

### MAIN ###
main() {
  parse_args "$@"
  setup_env
  check_dependencies
  check_cluster_reachability

  if [[ "$ACTION" == "uninstall" ]]; then
    uninstall_opencost
    exit 0
  fi

  # Step 1: Check for existing OpenCost
  detect_opencost

  if [[ "$OPENCOST_EXISTS" == "true" ]]; then
    detect_prometheus || true
    if [[ -n "$PROMETHEUS_ENDPOINT" ]]; then
      start_prometheus_portforward
      check_llmd_config
      local check_rc=$?
      stop_prometheus_portforward
    else
      log_warn "Could not determine Prometheus endpoint — skipping llm-d config checks."
      log_warn "Re-run with --prometheus-endpoint to enable full validation."
      local check_rc=0
    fi
    exit $check_rc
  fi

  # Step 2: Detect or install Prometheus
  if ! detect_prometheus; then
    prompt_prometheus_install
    install_prometheus
  fi

  [[ -n "$PROMETHEUS_ENDPOINT" ]] || die "Prometheus endpoint could not be determined. Use --prometheus-endpoint."

  # Step 3: Confirm pricing
  confirm_pricing

  # Step 4: Install
  install_opencost

  # Step 5: Wait for Prometheus to scrape, then check llm-d config
  echo ""
  start_prometheus_portforward
  wait_for_prometheus_scrape
  log_info "Running llm-d configuration checks..."
  check_llmd_config || true
  stop_prometheus_portforward
}

main "$@"
