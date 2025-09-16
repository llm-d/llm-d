#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

########################################
# Component versions
########################################
# Helm version
HELM_VER="v3.17.3"
# Helmdiff version
HELMDIFF_VERSION="v3.11.0"
# Helmfile version
HELMFILE_VERSION="1.1.3"
# chart-testing version
CT_VERSION="3.12.0"

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
  echo "Installing yq..."
  curl -sLo yq \
    "https://github.com/mikefarah/yq/releases/latest/download/yq_${OS}_${ARCH}"
  chmod +x yq
  sudo mv yq /usr/local/bin/yq
fi

if ! yq --version 2>&1 | grep -q 'mikefarah'; then
  echo "Detected yq is not mikefarah’s yq. Please uninstall your current yq and re-run this script."
  exit 1
fi
########################################
#  kubectl
########################################
if ! command -v kubectl &> /dev/null; then
  echo "Installing kubectl..."
  K8S_URL="https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)"
  curl -sLO "${K8S_URL}/bin/${OS}/${ARCH}/kubectl"
  if [[ "$OS" == "darwin" ]]; then
    sudo install -m 0755 kubectl /usr/local/bin/kubectl
  else
    sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
  fi
  rm kubectl
fi

########################################
#  Helm
########################################
if ! command -v helm &> /dev/null; then
  echo "Installing Helm..."
  TARBALL="helm-${HELM_VER}-${OS}-${ARCH}.tar.gz"
  curl -sLO "https://get.helm.sh/${TARBALL}"
  tar -zxvf "${TARBALL}"
  sudo mv "${OS}-${ARCH}/helm" /usr/local/bin/helm
  rm -rf "${OS}-${ARCH}" "${TARBALL}"
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
  echo "📦 helmfile not found. Installing ${HELMFILE_VERSION}..."
  if [[ "$OS" == "darwin" && "$ARCH" == "arm64" ]]; then
    ARCHIVE="helmfile_${HELMFILE_VERSION}_darwin_arm64.tar.gz"
  else
    ARCHIVE="helmfile_${HELMFILE_VERSION}_${OS}_${ARCH}.tar.gz"
  fi

  URL="https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/${ARCHIVE}"
  curl -sSL -o "/tmp/helmfile.tar.gz" "$URL"
  tar -xzf /tmp/helmfile.tar.gz -C /tmp
  sudo mv /tmp/helmfile /usr/local/bin/helmfile
  sudo chmod +x /usr/local/bin/helmfile
  rm /tmp/helmfile.tar.gz
fi

########################################
#  chart-testing (dev mode only)
########################################
if [[ "$DEV_MODE" == true ]]; then
  if ! command -v ct &> /dev/null; then
    echo "Installing chart-testing (ct)..."
    ARCHIVE="chart-testing_${CT_VERSION}_${OS}_${ARCH}.tar.gz"
    URL="https://github.com/helm/chart-testing/releases/download/v${CT_VERSION}/${ARCHIVE}"
    curl -sSL -o "/tmp/ct.tar.gz" "$URL"
    tar -xzf /tmp/ct.tar.gz -C /tmp
    sudo mv /tmp/ct /usr/local/bin/ct
    sudo chmod +x /usr/local/bin/ct
    rm /tmp/ct.tar.gz
  fi
fi

echo "✅ All tools installed successfully."
