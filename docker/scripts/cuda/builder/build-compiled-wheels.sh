#!/bin/bash
set -Eeux

# builds compiled extension wheels (FlashInfer, DeepEP, DeepGEMM)
#
# Required environment variables:
# - VIRTUAL_ENV: path to Python virtual environment
# - CUDA_MAJOR: CUDA major version (e.g., 12, 13)
# - CUDA_HOME: CUDA installation directory
# - FLASHINFER_REPO: FlashInfer git repo
# - FLASHINFER_VERSION: FlashInfer git ref
# - DEEPEP_REPO: DeepEP repository URL
# - DEEPEP_VERSION: DeepEP version tag
# - DEEPGEMM_REPO: DeepGEMM repository URL
# - DEEPGEMM_VERSION: DeepGEMM version tag
# - USE_SCCACHE: whether to use sccache (true/false)
# - TARGETPLATFORM: Docker buildx platform (e.g., linux/amd64, linux/arm64)
# Optional environment variables:
# - DEEPEP_GB200_REPO: GB200-specific DeepEP repository URL
# - DEEPEP_GB200_VERSION: GB200-specific DeepEP version tag

echo "BEGIN COMPILED WHEEL BUILDS LOGGING"

set -x

cd /tmp

. "${VIRTUAL_ENV}/bin/activate"
. /usr/local/bin/setup-sccache

# install build tools
uv pip install build cuda-python numpy setuptools-scm ninja cmake requests filelock tqdm

# Add CUDA stubs to library path for build-time linking (libcuda.so is not available in containers)
export LIBRARY_PATH="${CUDA_HOME}/lib64/stubs:${LIBRARY_PATH:-}"
# overwrite the TORCH_CUDA_ARCH_LIST for MoE kernels
export TORCH_CUDA_ARCH_LIST="9.0a;10.0+PTX"

# build FlashInfer wheel
uv pip uninstall flashinfer-python || true
git clone "${FLASHINFER_REPO}" flashinfer && cd flashinfer
git checkout -q "${FLASHINFER_VERSION}"
git submodule update --init --recursive
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf flashinfer

# build DeepEP wheel
# Install NVSHMEM Python package instead of using source-built version
# This avoids aarch64 static library linking issues
uv pip install nvidia-nvshmem-cu${CUDA_MAJOR}
# Unset NVSHMEM_DIR so DeepEP discovers NVSHMEM from the Python package
unset NVSHMEM_DIR

git clone "${DEEPEP_REPO}" deepep
cd deepep
git fetch origin "${DEEPEP_VERSION}" # Workaround for claytons floating commit
git checkout -q "${DEEPEP_VERSION}"
# Force NVSHMEM IBGDA constant to be extern in host-compiled TUs (prevents duplicate definition)
BACKUP_CXXFLAGS="${CXXFLAGS-}"
export CXXFLAGS="${CXXFLAGS:-} -D__NVSHMEM_NUMBA_SUPPORT__"
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf deepep
# restore CXXFLAGS exactly as it was (unset vs set)
if [ -n "${BACKUP_CXXFLAGS+x}" ]; then
  export CXXFLAGS="${BACKUP_CXXFLAGS}"
else
  unset CXXFLAGS
fi

# build GB200-specific DeepEP wheel (if configured)
if [ -n "${DEEPEP_GB200_REPO:-}" ] && [ -n "${DEEPEP_GB200_VERSION:-}" ]; then
  echo "=== Building GB200 DeepEP variant ==="
  mkdir -p /wheels-gb200
  git clone "${DEEPEP_GB200_REPO}" deepep-gb200
  cd deepep-gb200
  git fetch origin "${DEEPEP_GB200_VERSION}"
  git checkout -q "${DEEPEP_GB200_VERSION}"
  BACKUP_CXXFLAGS="${CXXFLAGS-}"
  export CXXFLAGS="${CXXFLAGS:-} -D__NVSHMEM_NUMBA_SUPPORT__"
  uv build --wheel --no-build-isolation --out-dir /wheels-gb200
  cd ..
  rm -rf deepep-gb200
  if [ -n "${BACKUP_CXXFLAGS+x}" ]; then
    export CXXFLAGS="${BACKUP_CXXFLAGS}"
  else
    unset CXXFLAGS
  fi
fi

# build DeepGEMM wheel
git clone "${DEEPGEMM_REPO}" deepgemm
cd deepgemm
git checkout -q "${DEEPGEMM_VERSION}"
git submodule update --init --recursive
uv build --wheel --no-build-isolation --out-dir /wheels
cd ..
rm -rf deepgemm

if [ "${USE_SCCACHE}" = "true" ]; then
  echo "=== Compiled wheels build complete - sccache stats ==="
  sccache --show-stats
fi
