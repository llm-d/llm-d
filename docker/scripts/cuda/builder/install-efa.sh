#!/bin/bash
set -Eeu
# special logging exception - do not use high level logging with EFA installer + entitlement

# purpose: Install EFA
# -------------------------------
# Optional environment variables:
# - ENABLE_EFA: Enable EFA installation (true/false, default: false)
# - EFA_INSTALLER_VERSION: Version of AWS EFA installer to download (default: 1.46.0)
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)
: "${ENABLE_EFA:=false}"
: "${EFA_INSTALLER_VERSION:=}"

# Skip EFA installation if not enabled, on Ubuntu, or missing installer version
if [ "${ENABLE_EFA}" != "true" ] || [ "$TARGETOS" = "ubuntu" ] || [ -z "${EFA_INSTALLER_VERSION}" ]; then
    echo "EFA installation skipped (ENABLE_EFA=${ENABLE_EFA}, TARGETOS=${TARGETOS})"
    # Create empty folder so Dockerfile COPY doesn't fail
    mkdir -p /tmp/efa_libs
    exit 0
fi

# Set EFA_PREFIX when EFA is enabled
EFA_PREFIX="/opt/amazon/efa"
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
# Only need base rpms to install EFA itself
if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
    rpm -ivh --nodeps /tmp/packages/rpms/builder/amd64/base/*.rpm
elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
    rpm -ivh --nodeps /tmp/packages/rpms/builder/arm64/base/*.rpm
fi

EFA_INSTALLER_URL="https://efa-installer.amazonaws.com"
EFA_TARBALL="aws-efa-installer-${EFA_INSTALLER_VERSION}.tar.gz"
EFA_WORKDIR="/tmp/efa"

echo "Installing AWS EFA (Elastic Fabric Adapter) ${EFA_INSTALLER_VERSION}"

mkdir -p "${EFA_WORKDIR}" /etc/ld.so.conf.d/
curl -fsSL "${EFA_INSTALLER_URL}/${EFA_TARBALL}" -o "${EFA_WORKDIR}/${EFA_TARBALL}"
tar -xzf "${EFA_WORKDIR}/${EFA_TARBALL}" -C "${EFA_WORKDIR}"

cd "${EFA_WORKDIR}/aws-efa-installer" && ./efa_installer.sh --skip-kmod -y

ldconfig
rm -rf "${EFA_WORKDIR}"

# Copy all EFA-installed libs to runtime
# - libefa.so*
# - libibverbs.so*
# - librdmacm.so*
mkdir -p /tmp/efa_libs
for efalib in libefa libibverbs librdmacm; do
    if ls /lib64/${efalib}.so* >/dev/null 2>&1; then
        cp -a /lib64/${efalib}.so* /tmp/efa_libs/ || true
    fi
done

cleanup_packages rhel
