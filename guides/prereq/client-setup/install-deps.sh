#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

set -euo pipefail

########################################
# Component versions
########################################
# Helm version
HELM_VER="v3.19.0"
# Helmdiff version
HELMDIFF_VERSION="v3.13.0"
# Helmfile version
HELMFILE_VERSION="1.2.1"
# chart-testing version
CT_VERSION="3.14.0"

########################################
#  Usage function
########################################
show_usage() {
  cat << EOF
Usage: $0 [OPTIONS]

Install essential tools for llm-d deployment.

OPTIONS:
  --dev     Install additional development tools (chart-testing)
  -h, --help     Show this help message and exit

EXAMPLES:
  $0             Install basic tools only
  $0 --dev       Install basic tools + development tools
  $0 --help      Show this help message

TOOLS INSTALLED:
  Basic tools:
    - git, curl, tar (system packages)
    - yq (YAML processor)
    - kubectl (Kubernetes CLI)
    - helm (Helm package manager)
    - helm diff plugin (optional but highly recommended)
    - helmfile (Helm deployment tool)

  Development tools (with --dev):
    - chart-testing (Helm chart testing tool)

EOF
}

########################################
#  Parse command line arguments
########################################
DEV_MODE=false
for arg in "$@"; do
  case $arg in
    --dev)
      DEV_MODE=true
      ;;
    -h|--help)
      show_usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      echo "Use --help for usage information."
      exit 1
      ;;
  esac
done

########################################
#  Helper: detect current OS / ARCH
########################################
OS=$(uname | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64) ARCH="amd64" ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

########################################
#  Helper: install a package via the
#  best available package manager
########################################
install_pkg() {
  PKG="$1"
  if [[ "$OS" == "linux" ]]; then
    if command -v apt &> /dev/null; then
      sudo apt-get install -y "$PKG"
    elif command -v dnf &> /dev/null; then
      sudo dnf install -y "$PKG"
    elif command -v yum &> /dev/null; then
      sudo yum install -y "$PKG"
    else
      echo "Unsupported Linux distro (no apt, dnf, or yum).";
      exit 1
    fi
  elif [[ "$OS" == "darwin" ]]; then
    if command -v brew &> /dev/null; then
      brew install "$PKG"
    else
      echo "Homebrew not found. Please install Homebrew or add manual install logic.";
      exit 1
    fi
  else
    echo "Unsupported OS: $OS";
    exit 1
  fi
}

########################################
# Helper: install binary from a URL
########################################
install_binary() {
  local url="$1"
  local bin_name="$2"
  local tmp_dir
  tmp_dir=$(mktemp -d)
  # Cleanup the temp directory on function return
  trap 'rm -rf -- "$tmp_dir"' RETURN

  echo "Installing ${bin_name}..."
  curl -sSL -o "${tmp_dir}/${bin_name}" "${url}"
  sudo install -m 0755 "${tmp_dir}/${bin_name}" "/usr/local/bin/${bin_name}"
}

########################################
# Helper: install binary from a tar.gz URL
########################################
install_from_tarball() {
  local url="$1"
  local bin_in_archive="$2"
  local bin_name="${3:-$bin_in_archive}"
  local tmp_dir
  tmp_dir=$(mktemp -d)
  # Cleanup the temp directory on function return
  trap 'rm -rf -- "$tmp_dir"' RETURN

  echo "Installing ${bin_name}..."
  curl -sSL "${url}" | tar -xz -C "${tmp_dir}"
  sudo install -m 0755 "${tmp_dir}/${bin_in_archive}" "/usr/local/bin/${bin_name}"
}

########################################
#  Base utilities
########################################
for pkg in git curl tar; do
  if ! command -v "$pkg" &> /dev/null; then
    install_pkg "$pkg"
  fi
done

########################################
#  yq (v4+)
########################################
if ! command -v yq &> /dev/null; then
  YQ_URL="https://github.com/mikefarah/yq/releases/latest/download/yq_${OS}_${ARCH}"
  install_binary "${YQ_URL}" "yq"
fi

if ! yq --version 2>&1 | grep -q 'mikefarah'; then
  echo "Detected yq is not mikefarah’s yq. Please uninstall your current yq and re-run this script."
  exit 1
fi
########################################
#  kubectl
########################################
if ! command -v kubectl &> /dev/null; then
  # Kubernetes version (latest stable)
  KUBE_VERSION=$(curl -sL https://dl.k8s.io/release/stable.txt)
  K8S_URL="https://dl.k8s.io/release/${KUBE_VERSION}/bin/${OS}/${ARCH}/kubectl"
  install_binary "${K8S_URL}" "kubectl"
fi

########################################
#  Helm
########################################
if ! command -v helm &> /dev/null; then
  TARBALL="helm-${HELM_VER}-${OS}-${ARCH}.tar.gz"
  HELM_URL="https://get.helm.sh/${TARBALL}"
  install_from_tarball "${HELM_URL}" "${OS}-${ARCH}/helm" "helm"
fi

########################################
#  Helm diff plugin
########################################
if ! helm plugin list | grep -q diff; then
  echo "📦 helm-diff plugin not found. Installing ${HELMDIFF_VERSION}..."
  helm plugin install --version "${HELMDIFF_VERSION}" https://github.com/databus23/helm-diff
fi

########################################
#  helmfile
########################################
if ! command -v helmfile &> /dev/null; then
  ARCHIVE="helmfile_${HELMFILE_VERSION}_${OS}_${ARCH}.tar.gz"
  HELMFILE_URL="https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/${ARCHIVE}"
  install_from_tarball "${HELMFILE_URL}" "helmfile"
fi

########################################
#  chart-testing (dev mode only)
########################################
if [[ "$DEV_MODE" == true ]]; then
  if ! command -v ct &> /dev/null; then
    ARCHIVE="chart-testing_${CT_VERSION}_${OS}_${ARCH}.tar.gz"
    CT_URL="https://github.com/helm/chart-testing/releases/download/v${CT_VERSION}/${ARCHIVE}"
    install_from_tarball "${CT_URL}" "ct"
  fi
fi

echo "✅ All tools installed successfully."
