#!/bin/bash
set -Eeu
# special logging exception - do not use high level logging with EFA installer + entitlement

# purpose: Install EFA (builder or runtime mode)
# -------------------------------
# Optional environment variables:
# - ENABLE_EFA: Enable EFA installation (true/false, default: false)
# - EFA_INSTALLER_VERSION: Version of AWS EFA installer to download (default: 1.46.0)
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)
# - EFA_MODE: Installation mode - either 'builder' or 'runtime' (default: builder)
: "${ENABLE_EFA:=false}"
: "${EFA_INSTALLER_VERSION:=}"
: "${EFA_MODE:=builder}"

# Validate mode
if [ "${EFA_MODE}" != "builder" ] && [ "${EFA_MODE}" != "runtime" ]; then
    echo "ERROR: EFA_MODE must be 'builder' or 'runtime', got: ${EFA_MODE}" >&2
    exit 1
fi

# Skip EFA installation if not enabled, on Ubuntu, or missing installer version
if [ "${ENABLE_EFA}" != "true" ] || [ "$TARGETOS" != "rhel" ]; then
    echo "EFA installation skipped (ENABLE_EFA=${ENABLE_EFA}, TARGETOS=${TARGETOS})"
    # Create empty folder so Dockerfile COPY doesn't fail (builder mode only)
    if [ "${EFA_MODE}" = "builder" ]; then
        mkdir -p /tmp/efa_libs /opt/amazon/efa
    fi
    exit 0
elif [ -z "${EFA_INSTALLER_VERSION}" ]; then
    echo "EFA installation selected but \"\${EFA_INSTALLER_VERSION}\" not provided."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# source shared utilities (check script dir first, fallback to /tmp for docker builds)
UTILS_SCRIPT="${SCRIPT_DIR}/../common/package-utils.sh"
[ ! -f "$UTILS_SCRIPT" ] && UTILS_SCRIPT="/tmp/package-utils.sh"
if [ ! -f "$UTILS_SCRIPT" ]; then
    echo "ERROR: package-utils.sh not found" >&2
    exit 1
fi
# shellcheck source=/dev/null
. "${UTILS_SCRIPT}"

update_system "${TARGETOS}"

# Install RPMs based on mode
if [ "${EFA_MODE}" = "builder" ]; then
    # Builder mode: only install base rpms
    if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/builder/amd64/base/*.rpm
    elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/builder/arm64/base/*.rpm
    fi
else
    # Runtime mode: install all runtime (base) rpms
    if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/runtime/amd64/*.rpm
    elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
        rpm -ivh --nodeps /tmp/packages/rpms/runtime/arm64/*.rpm
    fi
fi

EFA_INSTALLER_URL="https://efa-installer.amazonaws.com"
EFA_TARBALL="aws-efa-installer-${EFA_INSTALLER_VERSION}.tar.gz"
EFA_WORKDIR="/tmp/efa"

if [ "${EFA_MODE}" = "builder" ]; then
    echo "Installing AWS EFA (Elastic Fabric Adapter) ${EFA_INSTALLER_VERSION}"
else
    echo "Installing RDMA core from AWS EFA (Elastic Fabric Adapter) ${EFA_INSTALLER_VERSION}"
fi

# Builder mode needs /etc/ld.so.conf.d/, runtime mode doesn't
if [ "${EFA_MODE}" = "builder" ]; then
    mkdir -p "${EFA_WORKDIR}" /etc/ld.so.conf.d/
else
    mkdir -p "${EFA_WORKDIR}"
fi

curl -fsSL "${EFA_INSTALLER_URL}/${EFA_TARBALL}" -o "${EFA_WORKDIR}/${EFA_TARBALL}"
tar -xzf "${EFA_WORKDIR}/${EFA_TARBALL}" -C "${EFA_WORKDIR}"

# Run installer with appropriate flags based on mode
if [ "${EFA_MODE}" = "builder" ]; then
    cd "${EFA_WORKDIR}/aws-efa-installer" && ./efa_installer.sh --skip-kmod -y
    ldconfig
else
    cd "${EFA_WORKDIR}/aws-efa-installer" && ./efa_installer.sh --skip-kmod --minimal -y
fi

rm -rf "${EFA_WORKDIR}"

# Copy EFA libraries to /tmp/efa_libs for later use in runtime (builder mode only)
if [ "${EFA_MODE}" = "builder" ]; then
    mkdir -p /tmp/efa_libs
    for efalib in libefa libibverbs librdmacm; do
        if ls /lib64/${efalib}.so* >/dev/null 2>&1; then
            cp -a /lib64/${efalib}.so* /tmp/efa_libs/ || true
        fi
    done
fi

cleanup_packages rhel
