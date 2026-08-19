#!/bin/bash
set -Eeux

# builds and installs NVSHMEM from source with coreweave patch
#
# Optional environment variables:
# - ENABLE_EFA: Enable EFA support in NVSHMEM (true/false, default: false)
: "${ENABLE_EFA:=false}"
# Required environment variables (from Dockerfile ENV):
# - EFA_PREFIX: Path to EFA installation (used if ENABLE_EFA=true)
# Required environment variables:
# - TARGETOS: OS type (ubuntu or rhel)
# - CUDA_MAJOR: CUDA major version (e.g., 12)
# - CUDA_HOME: The path to your Cuda Runtime
# - NVSHMEM_USE_GIT: whether to use NVSHMEM git repo or nvidia developer source download (true/false) - defaults to true
# - NVSHMEM_REPO: if using git, what repo of NVSHMEM should be used
# - NVSHMEM_VERSION: NVSHMEM version to build (e.g., 3.3.20, or git ref if NVSHMEM_USE_GIT=true)
# - NVSHMEM_DIR: NVSHMEM installation directory
# - NVSHMEM_CUDA_ARCHITECTURES: CUDA architectures to build for
# - UCX_PREFIX: Path to UCX installation
# - VIRTUAL_ENV: Path to the virtual environment from which python will be pulled
# - USE_SCCACHE: whether to use sccache (true/false)
# - PYTHON_VERSION: Python version (e.g., 3.12)

cd /tmp

. /usr/local/bin/setup-sccache
. "${VIRTUAL_ENV}/bin/activate"

if [ "${NVSHMEM_USE_GIT}" = "true" ]; then
    git clone "${NVSHMEM_REPO}" nvshmem_src && cd nvshmem_src
    git checkout -q "${NVSHMEM_VERSION}"
else
    curl -fsSL \
    -o "nvshmem_src_cuda${CUDA_MAJOR}.tar.gz" \
    "https://developer.download.nvidia.com/compute/redist/nvshmem/${NVSHMEM_VERSION}/source/nvshmem_src_cuda${CUDA_MAJOR}-all-all-${NVSHMEM_VERSION}.tar.gz"

    tar -xf "nvshmem_src_cuda${CUDA_MAJOR}.tar.gz"
    cd nvshmem_src
fi

# No need for CKS patches if running on EKS only
if [ "${ENABLE_EFA}" != "true" ] || [ "$TARGETOS" = "ubuntu" ]; then
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
fi

# Enable EFA only for RHEL builds (Ubuntu EFA packages require 22.04+; gated on TARGETOS=rhel for now)
EFA_FLAGS=()
if [ "${ENABLE_EFA}" = "true" ] && [ "$TARGETOS" = "rhel" ]; then
    EFA_FLAGS=(
        -DNVSHMEM_LIBFABRIC_SUPPORT=1
        -DLIBFABRIC_HOME="${EFA_PREFIX}"
    )
fi

# Configure our build directory such that targets for specific nvshmem4py bindings exist
CMAKE_EXTRA_FLAGS+=(
    -DPython3_EXECUTABLE="${VIRTUAL_ENV}/bin/python"
    -DPython3_ROOT_DIR="${VIRTUAL_ENV}"
    -DPython3_FIND_STRATEGY=LOCATION
)

# Build the core library / SDK without the NVSHMEM4PY bindings
BUILD_NVSHMEM4PY_BINDINGS="OFF"
BUILD_PYTHON_DEVICE_LIB="OFF"
cmake -S . -B build -G Ninja \
    -DNVSHMEM_PREFIX="${NVSHMEM_DIR}" \
    -DCMAKE_CUDA_ARCHITECTURES="${NVSHMEM_CUDA_ARCHITECTURES}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_HOME}/bin/nvcc" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
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
    -DNVSHMEM_BUILD_TESTS="${NVSHMEM_BUILD_PERF_TESTS}" \
    -DNVSHMEM_BUILD_EXAMPLES=0 \
    -DNVSHMEM_BUILD_PYTHON_LIB=OFF \
    "${CMAKE_EXTRA_FLAGS[@]}" \
    "${EFA_FLAGS[@]}"

ninja -C build -j"${MAX_JOBS}"
cmake --install build
rm -rf build

cd /tmp
rm -rf nvshmem_src*

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== NVSHMEM build complete - sccache stats ==="
    sccache --show-stats
fi
