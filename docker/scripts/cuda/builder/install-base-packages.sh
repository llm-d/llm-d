#!/bin/bash
set -Eeux

# installs base packages, EPEL/universe repos, and CUDA repository
#
# Required environment variables:
# - PYTHON_VERSION: Python version to install (e.g., 3.12)
# - CUDA_MAJOR: CUDA major version (e.g., 12)
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)

TARGETOS="${TARGETOS:-rhel}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# source shared utilities (check script dir first, fallback to /tmp for docker builds)
UTILS_SCRIPT="${SCRIPT_DIR}/../common/package-utils.sh"
[ ! -f "$UTILS_SCRIPT" ] && UTILS_SCRIPT="/tmp/package-utils.sh"
if [ ! -f "$UTILS_SCRIPT" ]; then
    echo "ERROR: package-utils.sh not found" >&2
    exit 1
fi
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
    mapfile -t INSTALL_PKGS < <(load_layered_packages ubuntu "builder-packages.json" "cuda")
    install_packages ubuntu "${INSTALL_PKGS[@]}"
    cleanup_packages ubuntu
elif [ "$TARGETOS" = "rhel" ]; then
    setup_rhel_repos "$DOWNLOAD_ARCH"
    mapfile -t INSTALL_PKGS < <(load_layered_packages rhel "builder-packages.json" "cuda")
    install_packages rhel "${INSTALL_PKGS[@]}"
    
    # if using efa, we already installed hwloc as part of base RPMs
    if [ "${ENABLE_EFA}" != "true" ]; then
        # Install entitlement RPMs using rpm directly with --nodeps
        # The system glibc already provides all required symbols (verified: GLIBC_2.2.5 through 2.34)
        # but dnf fails to recognize this when installing from local RPM files
        if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
            rpm -ivh --nodeps /tmp/packages/rpms/builder/amd64/base/*.rpm
            rpm -ivh --nodeps /tmp/packages/rpms/builder/amd64/devel/*.rpm
        elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
            rpm -ivh --nodeps /tmp/packages/rpms/builder/arm64/base/*.rpm
            rpm -ivh --nodeps /tmp/packages/rpms/builder/arm64/devel/*.rpm
        fi
    else
        # EFA case, install just devel, base required as EFA dep
        if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
            rpm -ivh --nodeps /tmp/packages/rpms/builder/amd64/devel/*.rpm
        elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
            rpm -ivh --nodeps /tmp/packages/rpms/builder/arm64/devel/*.rpm
        fi
    fi

    cleanup_packages rhel
else
    echo "ERROR: Unsupported TARGETOS='$TARGETOS'. Must be 'ubuntu' or 'rhel'." >&2
    exit 1
fi
