#!/bin/bash
set -Eeu
# special logging exception - do not use high level logging with EFA installer + entitlement

# purpose: Install EFA
# -------------------------------
# Required docker secret mounts:
# - /run/secrets/subman_org: Subscription Manager Organization - used if on a ubi based image for entitlement
# - /run/secrets/subman_activation_key: Subscription Manager Activation key - used if on a ubi based image for entitlement
# -------------------------------
# Optional environment variables:
# - EFA_PREFIX: Path to include ld linkers to ensure that UCX and NVSHMEM can build against EFA and Libfacbric successfully. When empty will not run script.
# - EFA_INSTALLER_VERSION: Version of AWS EFA installer to download (default: 1.46.0 is the current latest release). When empty will not run script.
: "${EFA_PREFIX:=}"
: "${EFA_INSTALLER_VERSION:=}"
# Required environment variables:
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)


if [ "$TARGETOS" = "ubuntu" ] || [ -z "${EFA_PREFIX}" ]  || [ -z "${EFA_INSTALLER_VERSION}" ] ; then
    echo "Ubuntu image needs to be built against Ubuntu 20.04 and EFA only supports 22.04 and 24.04."
    # Create empty folder so Dockerfile COPY don't fail on Ubuntu
    mkdir -p "${EFA_PREFIX}" /tmp/efa_libs
    exit 0
fi

TARGETOS="${TARGETOS:-rhel}"
EFA_INSTALLER_VERSION="${EFA_INSTALLER_VERSION:-1.46.0}"
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

if [ "$TARGETOS" = "ubuntu" ]; then
    # efa uses apt instead of apt-get
    apt update -y
fi

EFA_INSTALLER_URL="https://efa-installer.amazonaws.com"
EFA_TARBALL="aws-efa-installer-${EFA_INSTALLER_VERSION}.tar.gz"
EFA_WORKDIR="/tmp/efa"

echo "Installing AWS EFA (Elastic Fabric Adapter) ${EFA_INSTALLER_VERSION}"

mkdir -p "${EFA_WORKDIR}" /etc/ld.so.conf.d/
curl -fsSL "${EFA_INSTALLER_URL}/${EFA_TARBALL}" -o "${EFA_WORKDIR}/${EFA_TARBALL}"
tar -xzf "${EFA_WORKDIR}/${EFA_TARBALL}" -C "${EFA_WORKDIR}"

cd "${EFA_WORKDIR}/aws-efa-installer" && ./efa_installer.sh --skip-kmod --no-verify -y

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
ensure_unregistered
