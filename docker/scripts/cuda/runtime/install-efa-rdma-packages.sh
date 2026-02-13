#!/bin/bash
set -Eeux
# special logging exception - do not use high level logging with EFA installer + entitlement

# purpose: Install EFA
# -------------------------------
# Required docker secret mounts:
# - /run/secrets/subman_org: Subscription Manager Organization - used if on a ubi based image for entitlement
# - /run/secrets/subman_activation_key: Subscription Manager Activation key - used if on a ubi based image for entitlement
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
    exit 0
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

if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then
    rpm -ivh --nodeps /tmp/packages/runtime/rpms/amd64/*.rpm
elif [ "${TARGETPLATFORM}" = "linux/arm64" ]; then
    rpm -ivh --nodeps /tmp/packages/runtime/rpms/arm64/*.rpm
fi

EFA_INSTALLER_URL="https://efa-installer.amazonaws.com"
EFA_TARBALL="aws-efa-installer-${EFA_INSTALLER_VERSION}.tar.gz"
EFA_WORKDIR="/tmp/efa"

echo "Installing RDMA core from AWS EFA (Elastic Fabric Adapter) ${EFA_INSTALLER_VERSION}"

mkdir -p "${EFA_WORKDIR}"

curl -fsSL "${EFA_INSTALLER_URL}/${EFA_TARBALL}" -o "${EFA_WORKDIR}/${EFA_TARBALL}"
tar -xzf "${EFA_WORKDIR}/${EFA_TARBALL}" -C "${EFA_WORKDIR}"

cd "${EFA_WORKDIR}/aws-efa-installer" && ./efa_installer.sh --skip-kmod --minimal -y

rm -rf "${EFA_WORKDIR}"

cleanup_packages rhel
