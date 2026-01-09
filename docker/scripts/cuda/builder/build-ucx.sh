#!/bin/bash
set -Eeu

# purpose: builds and installs UCX from source
# --------------------------------------------
# Optional docker secret mounts:
# - /run/secrets/aws_access_key_id: AWS access key ID for role that can only interact with SCCache S3 Bucket
# - /run/secrets/aws_secret_access_key: AWS secret access key for role that can only interact with SCCache S3 Bucket
# --------------------------------------------
# Required environment variables:
# - CUDA_HOME: Cuda runtime path to install UCX against
# - UCX_REPO: git remote to build UCX from
# - UCX_VERSION: git ref to build UCX from
# - UCX_PREFIX: prefix dir that contains installation path
# - USE_SCCACHE: whether to use sccache (true/false)

cd /tmp

. /usr/local/bin/setup-sccache

git clone "${UCX_REPO}" ucx && cd ucx
git checkout -q "${UCX_VERSION}" 

if [ "${USE_SCCACHE}" = "true" ]; then
    export CC="sccache gcc" CXX="sccache g++" 
fi

# Ubuntu image needs to be built against Ubuntu 20.04 and EFA only supports 22.04 and 24.04.
EFA_FLAG=""
if [ "$TARGETOS" = "rhel" ]; then
    EFA_FLAG="--with-efa"
fi

./autogen.sh 
./contrib/configure-release \
    --prefix="${UCX_PREFIX}" \
    --enable-shared \
    --disable-static \
    --disable-doxygen-doc \
    --enable-cma \
    --enable-devel-headers \
    --with-cuda="${CUDA_HOME}" \
    --with-verbs \
    --with-dm \
    --with-gdrcopy="/usr/local" \
    "${EFA_FLAG}" \
    --enable-mt

make -j$(nproc) 
make install-strip 
ldconfig 

cd /tmp && rm -rf /tmp/ucx 

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== UCX build complete - sccache stats ==="
    sccache --show-stats
fi
