#!/bin/bash
set -Eeux

# builds and installs LMCache and Infinistore from source
#
# Required environment variables:
# - USE_SCCACHE: whether to use sccache (true/false)
# - VIRTUAL_ENV: path to Python virtual environment
# - INFINISTORE_REPO: git repo to build Infinistore from
# - INFINISTORE_VERSION: git ref to build Infinistore from
# - LMCACHE_REPO: git repo to build LMCache from
# - LMCACHE_VERSION: git ref to build LMCache from
# Optional environment variables:
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)
# - TARGETOS: OS type (ubuntu or rhel)

cd /tmp

. /usr/local/bin/setup-sccache
. "${VIRTUAL_ENV}/bin/activate"

if [ "${USE_SCCACHE}" = "true" ]; then
    # Keep CC/CXX pointing at real compilers so torch doesn't think
    # "sccache" itself is the compiler (logging/ABI warning issue)
    export CC="gcc" CXX="g++" NVCC="nvcc"

    # Wrap gcc/g++ with sccache via PATH so caching still works
    WRAPDIR=/tmp/sccache-wrappers
    mkdir -p "$WRAPDIR"

    ln -sf "$(command -v sccache)" "$WRAPDIR/gcc"
    ln -sf "$(command -v sccache)" "$WRAPDIR/g++"

    # Ensure wrappers are picked up before system compilers
    export PATH="$WRAPDIR:$PATH"
fi
git clone "${INFINISTORE_REPO}" infinistore && cd infinistore
# pull tags for correct versioning on the wheel
git fetch --tags --force
git checkout -q "${INFINISTORE_VERSION}"
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf infinistore

git clone "${LMCACHE_REPO}" lmcache && cd lmcache
# pull tags for correct versioning on the wheel
git fetch --tags --force
git checkout -q "${LMCACHE_VERSION}"

# Prevent torch from whining when using sccache and misdetecting the compiler
# (logging-only issue, does not affect the actual build)
unset NINJA_STATUS
unset TORCH_LOGS
uv build -v --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf lmcache

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== LMCache and Infinistore build complete - sccache stats ==="
    sccache --show-stats
fi
