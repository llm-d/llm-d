#!/usr/bin/env bash
# Install a self-managed Gateway API control plane used by llm-d Gateway Mode.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-gateway-control-plane.sh [istio|agentgateway]
  install-gateway-control-plane.sh [apply|delete] [istio|agentgateway]

Environment overrides:
  ISTIO_VERSION          Istio version to install (default: 1.29.2)
  TARGET_ARCH            Istio download architecture override
  AGENTGATEWAY_VERSION   agentgateway chart version to install (default: v1.1.0)
EOF
}

require_command() {
  local command_name=$1

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "This script depends on ${command_name}. Please install it." >&2
    exit 1
  fi
}

run_istioctl() {
  require_command kubectl
  require_command curl
  require_command sh

  ISTIO_VERSION=${ISTIO_VERSION:-1.29.2}
  TMP_DIR=$(mktemp -d)
  trap 'rm -rf "${TMP_DIR}"' EXIT
  curl -fsSL https://istio.io/downloadIstio | ISTIO_VERSION="${ISTIO_VERSION}" TARGET_ARCH="${TARGET_ARCH:-}" sh -s -- -y -d "${TMP_DIR}"
  "${TMP_DIR}/istio-${ISTIO_VERSION}/bin/istioctl" "$@"
}

uninstall_helm_release() {
  local release_name=$1
  local namespace=$2

  if helm status "${release_name}" --namespace "${namespace}" >/dev/null 2>&1; then
    helm uninstall "${release_name}" --namespace "${namespace}"
  fi
}

MODE=apply
GATEWAY=${1:-istio}

case "${1:-}" in
  apply|delete)
    MODE=${1}
    GATEWAY=${2:-istio}
    if [[ $# -gt 2 ]]; then
      usage >&2
      exit 1
    fi
    ;;
  istio|agentgateway)
    if [[ $# -gt 2 ]]; then
      usage >&2
      exit 1
    fi
    if [[ $# -gt 1 ]]; then
      MODE=${2}
      if [[ "${MODE}" != "apply" && "${MODE}" != "delete" ]]; then
        usage >&2
        exit 1
      fi
    fi
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

case "${GATEWAY}" in
  istio)
    if [[ "${MODE}" == "apply" ]]; then
      run_istioctl install -y \
        --set values.pilot.env.ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true
    else
      run_istioctl uninstall --purge -y
      kubectl delete namespace istio-system --ignore-not-found
      kubectl delete gatewayclass istio istio-remote --ignore-not-found
    fi
    ;;
  agentgateway)
    require_command helm
    AGENTGATEWAY_VERSION=${AGENTGATEWAY_VERSION:-v1.1.0}
    if [[ "${MODE}" == "apply" ]]; then
      helm upgrade --install agentgateway-crds \
        oci://cr.agentgateway.dev/charts/agentgateway-crds \
        --namespace agentgateway-system \
        --create-namespace \
        --version "${AGENTGATEWAY_VERSION}"
      helm upgrade --install agentgateway \
        oci://cr.agentgateway.dev/charts/agentgateway \
        --namespace agentgateway-system \
        --create-namespace \
        --version "${AGENTGATEWAY_VERSION}" \
        --set inferenceExtension.enabled=true
    else
      uninstall_helm_release agentgateway agentgateway-system
      uninstall_helm_release agentgateway-crds agentgateway-system
      require_command kubectl
      kubectl delete namespace agentgateway-system --ignore-not-found
    fi
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
