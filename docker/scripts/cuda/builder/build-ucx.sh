#!/bin/bash
set -Eeux

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
# - TARGETOS: OS type (ubuntu or rhel)
# - MAX_JOBS: number of parallel jobs for the make build

cd /tmp

. /usr/local/bin/setup-sccache

git clone "${UCX_REPO}" ucx && cd ucx
git checkout -q "${UCX_VERSION}"

if [ "${USE_SCCACHE}" = "true" ]; then
    export CC="sccache gcc" CXX="sccache g++"
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
    --enable-mt

make -j"${MAX_JOBS}"
make install-strip
ldconfig

cd /tmp && rm -rf /tmp/ucx

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== UCX build complete - sccache stats ==="
    sccache --show-stats
fi
