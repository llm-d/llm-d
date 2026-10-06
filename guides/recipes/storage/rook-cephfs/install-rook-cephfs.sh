#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

set -euo pipefail

### GLOBALS ###
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOK_NAMESPACE="${ROOK_NAMESPACE:-rook-ceph}"
ROOK_CHART_REPO="${ROOK_CHART_REPO:-https://charts.rook.io/release}"
ROOK_CHART_VERSION="${ROOK_CHART_VERSION:-v1.20.2}"
STORAGE_NODE_LABEL="${STORAGE_NODE_LABEL:-llm-d.ai/ceph-storage=true}"
STORAGE_NODES="${STORAGE_NODES:-}"
ROOK_CLUSTER_EXTRA_VALUES="${ROOK_CLUSTER_EXTRA_VALUES:-}"
ACTION="install"
ASSUME_YES=false

CEPH_CLUSTER="rook-ceph"
CEPH_FILESYSTEM="kvcache-fs"
MIN_STORAGE_NODES=3

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Install or uninstall a Rook-managed Ceph cluster with a CephFS filesystem tuned
for KV-cache offloading, and the rook-cephfs-fast StorageClass.

Options:
  -n, --namespace NAME        Rook namespace (default: rook-ceph)
  -N, --nodes "A B C"         Label these nodes as storage nodes before installing
  -f, --values FILE           Extra values file for the rook-ceph-cluster chart
                              (network address ranges, device filter, ...)
  -u, --uninstall             Destroy the Ceph cluster and remove Rook
  -y, --yes                   Do not ask for confirmation on uninstall
  -h, --help                  Show this help and exit

Environment Variables:
  ROOK_NAMESPACE              Same as --namespace
  ROOK_CHART_REPO             Helm repository (default: https://charts.rook.io/release)
  ROOK_CHART_VERSION          Rook chart version (default: v1.20.2)
  STORAGE_NODE_LABEL          Label selecting the storage nodes (default: llm-d.ai/ceph-storage=true)
  STORAGE_NODES               Same as --nodes
  ROOK_CLUSTER_EXTRA_VALUES   Same as --values

Examples:
  $(basename "$0") -N "node-a node-b node-c"    # Label three nodes and install
  $(basename "$0") -f my-network.values.yaml    # Install with site-specific overrides
  $(basename "$0") -u                           # Destroy the Ceph cluster and remove Rook
EOF
}

log_info() { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

### UTILITIES ###
check_dependencies() {
  local cmd
  for cmd in helm kubectl; do
    command -v "${cmd}" &>/dev/null || fail "Required command not found: ${cmd}"
  done
  kubectl cluster-info &>/dev/null || fail "Cannot reach the Kubernetes cluster"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace) ROOK_NAMESPACE="$2"; shift 2 ;;
      -N|--nodes)     STORAGE_NODES="$2"; shift 2 ;;
      -f|--values)    ROOK_CLUSTER_EXTRA_VALUES="$2"; shift 2 ;;
      -u|--uninstall) ACTION="uninstall"; shift ;;
      -y|--yes)       ASSUME_YES=true; shift ;;
      -h|--help)      print_help; exit 0 ;;
      *)              fail "Unknown option: $1 (see --help)" ;;
    esac
  done
}

ceph() {
  kubectl exec -n "${ROOK_NAMESPACE}" deploy/rook-ceph-tools -- ceph "$@"
}

### INSTALL ###
label_storage_nodes() {
  if [[ -n "${STORAGE_NODES}" ]]; then
    log_info "Labelling storage nodes: ${STORAGE_NODES}"
    # shellcheck disable=SC2086  # STORAGE_NODES is a space-separated list
    kubectl label node ${STORAGE_NODES} "${STORAGE_NODE_LABEL}" --overwrite
  fi
  local count
  count=$(kubectl get node -l "${STORAGE_NODE_LABEL}" -o name | wc -l | tr -d ' ')
  if [[ "${count}" -lt "${MIN_STORAGE_NODES}" ]]; then
    fail "${count} node(s) carry ${STORAGE_NODE_LABEL}; Ceph needs at least ${MIN_STORAGE_NODES} for monitor quorum (use --nodes)"
  fi
  log_info "${count} storage nodes selected by ${STORAGE_NODE_LABEL}"
}

install() {
  label_storage_nodes

  log_info "Installing the Rook operator (${ROOK_CHART_VERSION})"
  helm upgrade --install rook-ceph rook-ceph \
    --repo "${ROOK_CHART_REPO}" --version "${ROOK_CHART_VERSION}" \
    -n "${ROOK_NAMESPACE}" --create-namespace \
    -f "${SCRIPT_DIR}/rook-operator.values.yaml"
  kubectl rollout status deployment/rook-ceph-operator -n "${ROOK_NAMESPACE}" --timeout=10m

  log_info "Creating the Ceph cluster and the ${CEPH_FILESYSTEM} filesystem"
  local extra_values=()
  if [[ -n "${ROOK_CLUSTER_EXTRA_VALUES}" ]]; then
    [[ -f "${ROOK_CLUSTER_EXTRA_VALUES}" ]] || fail "Values file not found: ${ROOK_CLUSTER_EXTRA_VALUES}"
    extra_values=(-f "${ROOK_CLUSTER_EXTRA_VALUES}")
  fi
  helm upgrade --install rook-ceph-cluster rook-ceph-cluster \
    --repo "${ROOK_CHART_REPO}" --version "${ROOK_CHART_VERSION}" \
    -n "${ROOK_NAMESPACE}" \
    --set operatorNamespace="${ROOK_NAMESPACE}" \
    -f "${SCRIPT_DIR}/rook-cluster.values.yaml" \
    ${extra_values[@]+"${extra_values[@]}"}

  log_info "Waiting for the Ceph cluster (OSD preparation takes several minutes)"
  kubectl wait "cephcluster/${CEPH_CLUSTER}" -n "${ROOK_NAMESPACE}" \
    --for=jsonpath='{.status.phase}'=Ready --timeout=30m
  kubectl wait "cephfilesystem/${CEPH_FILESYSTEM}" -n "${ROOK_NAMESPACE}" \
    --for=jsonpath='{.status.phase}'=Ready --timeout=15m
  kubectl rollout status deployment/rook-ceph-tools -n "${ROOK_NAMESPACE}" --timeout=5m

  # The single-copy KV-cache pool is intentional; silence only that warning.
  ceph health mute POOL_NO_REDUNDANCY --sticky

  log_info "Ceph is ready"
  ceph status
  kubectl get storageclass rook-cephfs-fast
}

### UNINSTALL ###
uninstall() {
  if [[ "${ASSUME_YES}" != true ]]; then
    local answer
    read -r -p "This destroys the Ceph cluster in '${ROOK_NAMESPACE}' and everything stored in it. Type 'destroy' to continue: " answer
    [[ "${answer}" == "destroy" ]] || fail "Aborted"
  fi

  if kubectl get "cephcluster/${CEPH_CLUSTER}" -n "${ROOK_NAMESPACE}" &>/dev/null; then
    log_info "Removing the Ceph cluster"
    kubectl patch "cephcluster/${CEPH_CLUSTER}" -n "${ROOK_NAMESPACE}" --type merge \
      -p '{"spec":{"cleanupPolicy":{"confirmation":"yes-really-destroy-data"}}}'
    if helm status rook-ceph-cluster -n "${ROOK_NAMESPACE}" &>/dev/null; then
      helm uninstall rook-ceph-cluster -n "${ROOK_NAMESPACE}"
    fi
    kubectl wait "cephcluster/${CEPH_CLUSTER}" -n "${ROOK_NAMESPACE}" --for=delete --timeout=20m
  fi

  log_info "Removing the Rook operator"
  if helm status rook-ceph -n "${ROOK_NAMESPACE}" &>/dev/null; then
    helm uninstall rook-ceph -n "${ROOK_NAMESPACE}"
  fi
  kubectl delete namespace "${ROOK_NAMESPACE}" --ignore-not-found
  kubectl label node -l "${STORAGE_NODE_LABEL}" "${STORAGE_NODE_LABEL%%=*}-"

  log_info "Done. The OSD devices keep their Ceph labels; see the README before reusing them."
}

main() {
  parse_args "$@"
  check_dependencies
  "${ACTION}"
}

main "$@"
