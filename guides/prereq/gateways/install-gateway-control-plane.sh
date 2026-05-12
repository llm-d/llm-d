#!/usr/bin/env bash
# Install a self-managed Gateway API control plane used by llm-d Gateway Mode.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-gateway-control-plane.sh [istio|agentgateway]
  install-gateway-control-plane.sh [apply|delete] [istio|agentgateway]
  install-gateway-control-plane.sh [istio|agentgateway] [apply|delete]

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

detect_istio_os() {
  case "$(uname -s)" in
    Linux)
      echo linux
      ;;
    Darwin)
      echo osx
      ;;
    *)
      echo "Unsupported Istio download OS: $(uname -s)" >&2
      exit 1
      ;;
  esac
}

detect_istio_arch() {
  local arch=${TARGET_ARCH:-$(uname -m)}

  case "${arch}" in
    amd64|x86_64)
      echo amd64
      ;;
    arm64|aarch64)
      echo arm64
      ;;
    armv7|armv7l)
      echo armv7
      ;;
    *)
      echo "Unsupported Istio download architecture: ${arch}" >&2
      exit 1
      ;;
  esac
}

verify_sha256() {
  local checksum_file=$1
  local checksum_dir

  checksum_dir=$(dirname "${checksum_file}")
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "${checksum_dir}" && sha256sum -c "$(basename "${checksum_file}")")
  elif command -v shasum >/dev/null 2>&1; then
    (cd "${checksum_dir}" && shasum -a 256 -c "$(basename "${checksum_file}")")
  else
    echo "This script depends on sha256sum or shasum. Please install one of them." >&2
    exit 1
  fi
}

run_istioctl() (
  require_command kubectl
  require_command curl
  require_command tar

  local istio_os
  local istio_arch
  local artifact
  local release_url
  local tmp_dir

  ISTIO_VERSION=${ISTIO_VERSION:-1.29.2}
  istio_os=$(detect_istio_os)
  istio_arch=$(detect_istio_arch)
  artifact="istioctl-${ISTIO_VERSION}-${istio_os}-${istio_arch}.tar.gz"
  release_url="https://github.com/istio/istio/releases/download/${ISTIO_VERSION}"
  tmp_dir=$(mktemp -d)
  trap 'rm -rf "${tmp_dir}"' EXIT

  curl -fsSL --retry 3 -o "${tmp_dir}/${artifact}" "${release_url}/${artifact}"
  curl -fsSL --retry 3 -o "${tmp_dir}/${artifact}.sha256" "${release_url}/${artifact}.sha256"
  verify_sha256 "${tmp_dir}/${artifact}.sha256"
  tar -xzf "${tmp_dir}/${artifact}" -C "${tmp_dir}"
  "${tmp_dir}/istioctl" "$@"
)

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
