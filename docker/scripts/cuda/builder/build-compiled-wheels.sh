#!/bin/bash
set -Eeu

# builds compiled extension wheels (FlashInfer, DeepEP, DeepGEMM, pplx-kernels)
#
# Required environment variables:
# - VIRTUAL_ENV: path to Python virtual environment
# - CUDA_MAJOR: CUDA major version (e.g., 12)
# - NVSHMEM_DIR: NVSHMEM installation directory (not needed on ARM64)
# - FLASHINFER_VERSION: FlashInfer version tag
# - DEEPEP_REPO: DeepEP repository URL
# - DEEPEP_VERSION: DeepEP version tag
# - DEEPGEMM_REPO: DeepGEMM repository URL
# - DEEPGEMM_VERSION: DeepGEMM version tag
# - PPLX_KERNELS_REPO: pplx-kernels repository URL
# - PPLX_KERNELS_SHA: pplx-kernels commit SHA
# - USE_SCCACHE: whether to use sccache (true/false)
# Optional environment variables:
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)

# shellcheck source=/dev/null
source "${VIRTUAL_ENV}/bin/activate"
# shellcheck source=/dev/null
source /usr/local/bin/setup-sccache

# install build tools
uv pip install build cuda-python numpy setuptools-scm ninja

# install nvshmem4py (skip on ARM64)
if [ "${TARGETPLATFORM:-linux/amd64}" != "linux/arm64" ]; then
    uv pip install /wheels/nvshmem4py_cu"${CUDA_MAJOR}"-*.whl
else
    echo "Skipping nvshmem4py installation on ARM64"
fi

cd /tmp

# build FlashInfer wheel
uv pip uninstall flashinfer-python || true
git clone https://github.com/flashinfer-ai/flashinfer.git
cd flashinfer
git checkout -q "${FLASHINFER_VERSION}"
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf flashinfer

# build DeepEP wheel (skip on ARM64 - requires nvshmem)
if [ "${TARGETPLATFORM:-linux/amd64}" != "linux/arm64" ]; then
    git clone "${DEEPEP_REPO}" deepep
    cd deepep
    git checkout -q "${DEEPEP_VERSION}"
    uv build --wheel --no-build-isolation --out-dir /wheels
    cd ..
    rm -rf deepep
else
    echo "Skipping DeepEP build on ARM64"
fi

# build DeepGEMM wheel
git clone "${DEEPGEMM_REPO}" deepgemm
cd deepgemm
git checkout -q "${DEEPGEMM_VERSION}"
git submodule update --init --recursive
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf deepgemm

# build pplx-kernels wheel (skip on ARM64 - requires nvshmem)
if [ "${TARGETPLATFORM:-linux/amd64}" != "linux/arm64" ]; then
    git clone "${PPLX_KERNELS_REPO}" pplx-kernels
    cd pplx-kernels
    git checkout "${PPLX_KERNELS_SHA}"
    NVSHMEM_PREFIX="${NVSHMEM_DIR}" uv build --wheel --out-dir /wheels
    cd ..
    rm -rf pplx-kernels
else
    echo "Skipping pplx-kernels build on ARM64"
fi

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== Compiled wheels build complete - sccache stats ==="
    sccache --show-stats
fi
