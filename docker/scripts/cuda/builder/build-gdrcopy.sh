#!/bin/bash
set -Eeu

# builds and installs gdrcopy from source
#
# Required environment variables:
# - USE_SCCACHE: whether to use sccache (true/false)
# - GDRCOPY_REPO: git repo to build GDRCopy from
# - GDRCOPY_VERSION: git ref to build GDRCopy from
# - GDRCOPY_PREFIX: location to install GDR Copy to
# Optional environment variables:
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)
# - TARGETOS: OS type (ubuntu or rhel)

cd /tmp

. /usr/local/bin/setup-sccache

# determine architecture and library directory for gdrcopy build
UUARCH=""
LIBDIR=""
case "${TARGETPLATFORM:-linux/amd64}" in
  linux/arm64)
    UUARCH="aarch64"
    if [ "${TARGETOS:-rhel}" = "ubuntu" ]; then
        LIBDIR="/usr/lib/aarch64-linux-gnu"
    else
        LIBDIR="/usr/lib64"
    fi
    ;;
  linux/amd64)
    UUARCH="x64"
    if [ "${TARGETOS:-rhel}" = "ubuntu" ]; then
        LIBDIR="/usr/lib/x86_64-linux-gnu"
    else
        LIBDIR="/usr/lib64"
    fi
    ;;
  *) echo "Unsupported TARGETPLATFORM: ${TARGETPLATFORM}" >&2; exit 1 ;;
esac

git clone "${GDRCOPY_REPO}" gdrcopy && cd gdrcopy
git checkout -q "${GDRCOPY_VERSION}"

if [ "${USE_SCCACHE}" = "true" ]; then
    export CC="sccache gcc" CXX="sccache g++"
fi

ARCH="${UUARCH}" PREFIX=/usr/local DESTLIB=/usr/local/lib make lib_install

cp src/libgdrapi.so.2.* "${LIBDIR}/"
# also stage in /tmp for runtime stage to copy
mkdir -p /tmp/gdrcopy_libs
cp src/libgdrapi.so.2.* /tmp/gdrcopy_libs/
ldconfig

cd ..
rm -rf gdrcopy

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== gdrcopy build complete - sccache stats ==="
    sccache --show-stats
fi
