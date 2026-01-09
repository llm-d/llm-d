#!/bin/bash
set -Eeux

# builds and installs NVSHMEM from source with coreweave patch
#
# Required environment variables:
# - TARGETOS: OS type (ubuntu or rhel)
# - CUDA_MAJOR: CUDA major version (e.g., 12)
# - CUDA_HOME: The path to your Cuda Runtime
# - NVSHMEM_USE_GIT: whether to use NVSHMEM git repo or nvidia developer source download (true/false) - defaults to true
# - NVSHMEM_REPO: if using git, what repo of NVSHMEM should be used
# - NVSHMEM_VERSION: NVSHMEM version to build (e.g., 3.3.20, or git ref if NVSHMEM_USE_GIT=true)
# - NVSHMEM_DIR: NVSHMEM installation directory
# - NVSHMEM_CUDA_ARCHITECTURES: CUDA architectures to build for
# - EFA_PREFIX: Path to EFA installation
# - UCX_PREFIX: Path to UCX installation
# - VIRTUAL_ENV: Path to the virtual environment from which python will be pulled
# - USE_SCCACHE: whether to use sccache (true/false)

cd /tmp

. /usr/local/bin/setup-sccache
. "${VIRTUAL_ENV}/bin/activate"

if [ "${NVSHMEM_USE_GIT}" = "true" ]; then
    git clone "${NVSHMEM_REPO}" nvshmem_src && cd nvshmem_src
    git checkout -q "${NVSHMEM_VERSION}"
else
    wget "https://developer.download.nvidia.com/compute/redist/nvshmem/${NVSHMEM_VERSION}/source/nvshmem_src_cuda12-all-all-${NVSHMEM_VERSION}.tar.gz" \
    -O "nvshmem_src_cuda${CUDA_MAJOR}.tar.gz"
    tar -xf "nvshmem_src_cuda${CUDA_MAJOR}.tar.gz"
    cd nvshmem_src
fi

# Prior to NVSHMEM_VERSION 3.4.5 we have to carry a set of patches for device renaming.
# For more info, see: https://github.com/NVIDIA/nvshmem/releases/tag/v3.4.5-0, specifically regarding NVSHMEM_HCA_PREFIX
for i in /tmp/patches/cks_nvshmem"${NVSHMEM_VERSION}".patch /tmp/patches/nvshmem_zero_ibv_ah_attr_"${NVSHMEM_VERSION}".patch; do
    if [[ -f $i ]]; then
        echo "Applying patch: $i"
        git apply $i
    else
        echo "Unable to find patch matching nvshmem version ${NVSHMEM_VERSION}: $i"
    fi
done

mkdir -p build && cd build

# Ubuntu image needs to be built against Ubuntu 20.04 and EFA only supports 22.04 and 24.04.
EFA_FLAGS=("")
if [ "$TARGETOS" = "rhel" ]; then
    EFA_FLAGS=(
        -DNVSHMEM_LIBFABRIC_SUPPORT=1
        -DLIBFABRIC_HOME="${EFA_PREFIX}"
    )
fi

cmake \
    -G Ninja \
    -DNVSHMEM_PREFIX="${NVSHMEM_DIR}" \
    -DCMAKE_CUDA_ARCHITECTURES="${NVSHMEM_CUDA_ARCHITECTURES}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_HOME}/bin/nvcc" \
    -DNVSHMEM_PMIX_SUPPORT=0 \
    -DNVSHMEM_IBRC_SUPPORT=1 \
    -DNVSHMEM_IBGDA_SUPPORT=1 \
    -DNVSHMEM_IBDEVX_SUPPORT=1 \
    -DNVSHMEM_UCX_SUPPORT=1 \
    -DUCX_HOME="${UCX_PREFIX}" \
    -DNVSHMEM_SHMEM_SUPPORT=0 \
    -DNVSHMEM_USE_GDRCOPY=1 \
    -DGDRCOPY_HOME="/usr/local" \
    -DNVSHMEM_MPI_SUPPORT=0 \
    -DNVSHMEM_USE_NCCL=0 \
    -DNVSHMEM_BUILD_TESTS=0 \
    -DNVSHMEM_BUILD_EXAMPLES=0 \
    ${EFA_FLAGS[@]} \
    ..

ninja -j"$(nproc)"
ninja install

# copy python wheel to /wheels
cp "${NVSHMEM_DIR}"/lib/python/dist/nvshmem4py_cu"${CUDA_MAJOR}"-*-cp"${PYTHON_VERSION/./}"-cp"${PYTHON_VERSION/./}"-manylinux*.whl /wheels/

cd /tmp
rm -rf nvshmem_src*

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== NVSHMEM build complete - sccache stats ==="
    sccache --show-stats
fi
