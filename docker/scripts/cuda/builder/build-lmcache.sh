#!/bin/bash
set -Eeu

# builds and installs gdrcopy from source
#
# Required environment variables:
# - USE_SCCACHE: whether to use sccache (true/false)
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
    export CC="sccache gcc" CXX="sccache g++" NVCC="sccache nvcc"
fi

git clone "${INFINISTORE_REPO}" infinistore && cd infinistore
git checkout -q "${INFINISTORE_VERSION}"
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf infinistore

git clone "${LMCACHE_REPO}" lmcache && cd lmcache
git checkout -q "${LMCACHE_VERSION}"
uv build --wheel --no-build-isolation --out-dir /wheels  && \
cd ..
rm -rf lmcache

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== LMCache and Infinistore build complete - sccache stats ==="
    sccache --show-stats
fi
