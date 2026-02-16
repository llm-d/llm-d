#!/bin/bash
set -Eeux

# builds and installs NVSHMEM from source with coreweave patch
#
# Optional environment variables:
# - ENABLE_EFA: Enable EFA support in NVSHMEM (true/false, default: false)
: "${ENABLE_EFA:=false}"
# - BUILD_DEBUG: whether to build with debug symbols and logging (true/false) - defaults to false
: "${BUILD_DEBUG:=false}"
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

if [ "${BUILD_DEBUG}" = "true" ]; then
    # Disable sccache for nvshmem build in debug mode for nvcc + sccache + cmake weirdness. 
    # Not an issue for regular builds, only for BUILD_DEBUG=true
    export USE_SCCACHE="false"
fi

. /usr/local/bin/setup-sccache
. "${VIRTUAL_ENV}/bin/activate"

if [ "${NVSHMEM_USE_GIT}" = "true" ]; then
    git clone "${NVSHMEM_REPO}" nvshmem_src && cd nvshmem_src
    git checkout -q "${NVSHMEM_VERSION}"
else
    curl -fsSL \
    -o "nvshmem_src_cuda${CUDA_MAJOR}.tar.gz" \
    "https://developer.download.nvidia.com/compute/redist/nvshmem/${NVSHMEM_VERSION}/source/nvshmem_src_cuda12-all-all-${NVSHMEM_VERSION}.tar.gz"

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

# Ubuntu image needs to be built against Ubuntu 20.04 and EFA only supports 22.04 and 24.04.
EFA_FLAGS=()
if [ "${ENABLE_EFA}" = "true" ] && [ "$TARGETOS" = "rhel" ]; then
    EFA_FLAGS=(
        -DNVSHMEM_LIBFABRIC_SUPPORT=1
        -DLIBFABRIC_HOME="${EFA_PREFIX}"
    )
fi
# Configure debug build options
DEBUG_FLAGS=()
CMAKE_EXTRA_FLAGS=()

NVSHMEM_BUILD_PERF_TESTS=0 # Nvshmem perf test binaries, off by default on with debug
if [ "${BUILD_DEBUG}" = "true" ]; then
    echo "=== Building NVSHMEM with debug symbols and logging enabled ==="

    CMAKE_EXTRA_FLAGS+=(
        -DCMAKE_COMPILE_WARNING_AS_ERROR=OFF
    )

    DEBUG_FLAGS=(
        -DCMAKE_BUILD_TYPE=RelWithDebInfo
        -DNVSHMEM_DEBUG=ON
        -DNVSHMEM_DEVEL=ON
        -DNVSHMEM_VERBOSE=ON
    )

    # Host compiler: keep warnings, but don't fail the build on maybe-uninitialized
    # Use *no-error* rather than *no-warning* so you still see it in logs.
    CMAKE_EXTRA_FLAGS+=(
        -DCMAKE_C_FLAGS_DEBUG="-Wno-error=maybe-uninitialized"
        -DCMAKE_CXX_FLAGS_DEBUG="-Wno-error=maybe-uninitialized"
        -DCMAKE_C_FLAGS_RELWITHDEBINFO="-Wno-error=maybe-uninitialized"
        -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-Wno-error=maybe-uninitialized"
    )

    # NVCC: ensure we don't get broken "-Werror all-warnings" behavior in debug.
    # This is the safest knob if NVSHMEM is injecting "-Werror all-warnings".
    # We can also explicitly clear/override CUDA flags in this config.
    CMAKE_EXTRA_FLAGS+=(
        -DCMAKE_CUDA_FLAGS_RELWITHDEBINFO=""
        -DCMAKE_CUDA_FLAGS_DEBUG=""
    )

    # If NVSHMEM insists on adding "-Werror all-warnings" despite NVSHMEM_WERROR=OFF,
    # we can add a *counter-flag* at the end to neutralize it.
    # Unfortunately, NVCC doesn't have a universal "-Wno-error" for that form,
    # so we prefer removing it at the source (NVSHMEM_WERROR) and/or emptying CUDA flags.

    NVSHMEM_BUILD_PERF_TESTS=1
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
    -DNVSHMEM_BUILD_PYTHON_LIB="${BUILD_NVSHMEM4PY_BINDINGS}" \
    -DNVSHMEM_BUILD_PYTHON_DEVICE_LIB="${BUILD_PYTHON_DEVICE_LIB}" \
    "${DEBUG_FLAGS[@]}" \
    "${CMAKE_EXTRA_FLAGS[@]}" \
    "${EFA_FLAGS[@]}"

ninja -C build -j"$(nproc)"
cmake --install build
rm -rf build

# overwrite build perf tests for the 4py bindings
NVSHMEM_BUILD_PERF_TESTS=0
# re-build the build directory with nvshmem4py targets and explicitly call the right one.
BUILD_NVSHMEM4PY_BINDINGS="ON"
BUILD_PYTHON_DEVICE_LIB="ON"
cmake -S . -B build -G Ninja \
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
    -DNVSHMEM_BUILD_TESTS="${NVSHMEM_BUILD_PERF_TESTS}" \
    -DNVSHMEM_BUILD_EXAMPLES=0 \
    -DNVSHMEM_BUILD_PYTHON_LIB="${BUILD_NVSHMEM4PY_BINDINGS}" \
    -DNVSHMEM_BUILD_PYTHON_DEVICE_LIB="${BUILD_PYTHON_DEVICE_LIB}" \
    "${DEBUG_FLAGS[@]}" \
    "${CMAKE_EXTRA_FLAGS[@]}" \
    "${EFA_FLAGS[@]}"

# explicitly build one target after re-setting up build with all bindings options (default is via discovery)
ninja -C build "build_nvshmem4py_wheel_cu${CUDA_MAJOR}_${PYTHON_VERSION}"

# Parse our python version to platforming tag, eg: 3.12 --> 312
PYTAG="cp${PYTHON_VERSION/./}"
NVSHMEM4PY_WHEEL="$(find build/dist -maxdepth 1 -type f \
  -name "nvshmem4py_cu${CUDA_MAJOR}-*-${PYTAG}-${PYTAG}-manylinux*.whl" \
  | head -n 1)"

if [ -z "${NVSHMEM4PY_WHEEL}" ]; then
  echo "ERROR: nvshmem4py wheel not found in build/dist"
  echo "  expected pattern: nvshmem4py_cu${CUDA_MAJOR}-*-${PYTAG}-${PYTAG}-manylinux*.whl"
  echo "  contents of build/dist:"
  ls -la build/dist || true
  exit 1
fi

cp -v "${NVSHMEM4PY_WHEEL}" /wheels/

cd /tmp
rm -rf nvshmem_src*

if [ "${USE_SCCACHE}" = "true" ]; then
    echo "=== NVSHMEM build complete - sccache stats ==="
    sccache --show-stats
fi
