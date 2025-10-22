#!/bin/bash
set -Eeu

# builds and installs gdrcopy from source
#
# Required environment variables:
# - USE_SCCACHE: whether to use sccache (true/false)
# Optional environment variables:
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)

# shellcheck source=/dev/null
source /usr/local/bin/setup-sccache

# determine architecture for gdrcopy build
UUARCH=""
case "${TARGETPLATFORM:-linux/amd64}" in
  linux/arm64) UUARCH="aarch64" ;;
  linux/amd64) UUARCH="x64" ;;
  *) echo "Unsupported TARGETPLATFORM: ${TARGETPLATFORM}" >&2; exit 1 ;;
esac

git clone https://github.com/NVIDIA/gdrcopy.git
cd gdrcopy

if [ "${USE_SCCACHE}" = "true" ]; then
    export CC="sccache gcc" CXX="sccache g++"
fi
ARCH="${UUARCH}" PREFIX=/usr/local DESTLIB=/usr/local/lib make lib_install

cp src/libgdrapi.so.2.* /usr/lib64/
ldconfig

cd ..
rm -rf gdrcopy

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== gdrcopy build complete - sccache stats ==="
    sccache --show-stats
fi
