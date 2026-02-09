#!/bin/bash
set -Eeu

# installs runtime packages for CUDA image
#
# Required docker secret mounts:
# - /run/secrets/subman_org: Subscription Manager Organization - used if on a ubi based image for entitlement
# - /run/secrets/subman_activation_key: Subscription Manager Activation key - used if on a ubi based image for entitlement
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
# shellcheck source=docker/scripts/cuda/common/package-utils.sh
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
    cleanup_packages ubuntu

elif [ "$TARGETOS" = "rhel" ]; then
    setup_rhel_repos "$DOWNLOAD_ARCH"
    mapfile -t INSTALL_PKGS < <(load_layered_packages rhel "runtime-packages.json" "cuda")
    install_packages rhel "${INSTALL_PKGS[@]}"
    # Install hwloc-libs from entitlement RPMs using rpm directly with --nodeps
    # The system glibc already provides all required symbols (verified: GLIBC_2.2.5 through 2.34)
    # but dnf fails to recognize this when installing from local RPM files
    if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/amd64/hwloc-libs*.rpm
    elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/arm64/hwloc-libs*.rpm
    fi
    cleanup_packages rhel
else
    echo "ERROR: Unsupported TARGETOS='$TARGETOS'. Must be 'ubuntu' or 'rhel'." >&2
    exit 1
fi
