#!/bin/bash
set -Eeux

# installs runtime packages for CUDA image
#
# Required environment variables:
# - PYTHON_VERSION: Python version to install (e.g., 3.12)
# - CUDA_MAJOR: CUDA major version (e.g., 12)
# - CUDA_MINOR: CUDA minor version (e.g., 9)
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)
# - TARGETPLATFORM: TARGET PLATFORM - either linux/amd64 or linux/arm64

TARGETOS="${TARGETOS:-rhel}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# source shared utilities (check script dir first, fallback to /tmp for docker builds)
UTILS_SCRIPT="${SCRIPT_DIR}/../common/package-utils.sh"
[ ! -f "$UTILS_SCRIPT" ] && UTILS_SCRIPT="/tmp/package-utils.sh"
if [ ! -f "$UTILS_SCRIPT" ]; then
    echo "ERROR: package-utils.sh not found" >&2
    exit 1
fi
# shellcheck source=/dev/null
. "$UTILS_SCRIPT"

DOWNLOAD_ARCH=$(get_download_arch)

# install jq first (required to parse package mappings)
if [ "$TARGETOS" = "ubuntu" ]; then
    apt-get update -qq
    apt-get install -y jq
elif [ "$TARGETOS" = "rhel" ]; then
    dnf -q update -y
    dnf -q install -y jq
fi

# main installation logic
if [ "$TARGETOS" = "ubuntu" ]; then
    setup_ubuntu_repos
    mapfile -t INSTALL_PKGS < <(load_layered_packages ubuntu "runtime-packages.json" "cuda")
    install_packages ubuntu "${INSTALL_PKGS[@]}"
    # This if statement is redundent right now (currently no EFA on our ubuntu images),
    # but its included in case we figure out EFA on Ubuntu images.
    if [ "${ENABLE_EFA}" != "true" ]; then
        mapfile -t INSTALL_RDMA_PKGS < <(load_layered_packages ubuntu "runtime-rdma-packages.json" "cuda")
        install_packages ubuntu "${INSTALL_RDMA_PKGS[@]}"
    fi
    cleanup_packages ubuntu
elif [ "$TARGETOS" = "rhel" ]; then
    setup_rhel_repos "$DOWNLOAD_ARCH"
    mapfile -t INSTALL_PKGS < <(load_layered_packages rhel "runtime-packages.json" "cuda")
    install_packages rhel "${INSTALL_PKGS[@]}"
    # # If not using EFA, runtime-rdma-packages file owns rdma installation
    if [ "${ENABLE_EFA}" != "true" ]; then
        mapfile -t INSTALL_RDMA_PKGS < <(load_layered_packages rhel "runtime-rdma-packages.json" "cuda")
        install_packages rhel "${INSTALL_RDMA_PKGS[@]}"
    fi
    cleanup_packages rhel
else
    echo "ERROR: Unsupported TARGETOS='$TARGETOS'. Must be 'ubuntu' or 'rhel'." >&2
    exit 1
fi
