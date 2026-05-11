#!/usr/bin/env bash
# Install a self-managed Gateway API control plane used by llm-d Gateway Mode.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-gateway-control-plane.sh [istio|agentgateway]

Environment overrides:
  ISTIO_VERSION          Istio version to install (default: 1.29.2)
  AGENTGATEWAY_VERSION   agentgateway chart version to install (default: v1.1.0)
EOF
}

GATEWAY=${1:-istio}

case "${GATEWAY}" in
  istio)
    ISTIO_VERSION=${ISTIO_VERSION:-1.29.2}
    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TMP_DIR}"' EXIT
    curl -fsSL https://istio.io/downloadIstio | ISTIO_VERSION="${ISTIO_VERSION}" TARGET_ARCH="${TARGET_ARCH:-}" sh -s -- -y -d "${TMP_DIR}"
    "${TMP_DIR}/istio-${ISTIO_VERSION}/bin/istioctl" install -y \
      --set values.pilot.env.ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true
    ;;
  agentgateway)
    AGENTGATEWAY_VERSION=${AGENTGATEWAY_VERSION:-v1.1.0}
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
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
