#!/bin/bash
set -Eeux

# purpose: builds NIXL from source, gated by `BUILD_NIXL_FROM_SOURCE`
#
# Optional environment variables:
# - EFA_PREFIX: Path to Libfabric installation
: "${EFA_PREFIX:=}"
# Required environment variables:
# - BUILD_NIXL_FROM_SOURCE: if nixl should be installed by vLLM or has been built from source in the builder stages
# - NIXL_REPO: Git repo to use for NIXL
# - NIXL_VERSION: Git ref to use for NIXL
# - NIXL_PREFIX: Path to install NIXL to
# - UCX_PREFIX: Path to UCX installation
# - VIRTUAL_ENV: Path to the virtual environment
# - USE_SCCACHE: whether to use sccache (true/false)
# - TARGETOS: OS type (ubuntu or rhel)

if [ "${BUILD_NIXL_FROM_SOURCE}" = "false" ]; then
    echo "NIXL will be installed be vLLM and not built from source."
    exit 0
fi

cd /tmp

. /usr/local/bin/setup-sccache
. "${VIRTUAL_ENV}/bin/activate"

git clone "${NIXL_REPO}" nixl && cd nixl
git checkout -q "${NIXL_VERSION}"

if [ "${USE_SCCACHE}" = "true" ]; then
    export CC="sccache gcc" CXX="sccache g++" NVCC="sccache nvcc"
fi

# Ubuntu image needs to be built against Ubuntu 20.04 and EFA only supports 22.04 and 24.04.
EFA_FLAG=""
if [ "$TARGETOS" = "rhel" ] && [ -n "${EFA_PREFIX}" ]; then
    EFA_FLAG="-Dlibfabric_path=${EFA_PREFIX}"
fi

meson setup build \
    --prefix="${NIXL_PREFIX}" \
    -Dbuildtype=release \
    -Ducx_path="${UCX_PREFIX}" \
    "${EFA_FLAG}" \
    -Dinstall_headers=true

cd build
ninja
ninja install
cd ..
. ${VIRTUAL_ENV}/bin/activate
python -m build --no-isolation --wheel -o /wheels

cp build/src/bindings/python/nixl-meta/nixl-*-py3-none-any.whl /wheels/

rm -rf build

cd /tmp && rm -rf /tmp/nixl
