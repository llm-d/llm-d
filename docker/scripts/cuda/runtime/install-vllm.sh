#!/bin/bash
set -Eeu

# installs vllm and dependencies in runtime stage
#
# Optional environment variabls:
# - SUPPRESS_PYTHON_OUTPUT: If we should suppres vLLM installation logs
: "${SUPPRESS_PYTHON_OUTPUT:=}"
# Required environment variables:
# - VLLM_REPO: vLLM git repository URL
# - VLLM_COMMIT_SHA: vLLM commit SHA to checkout
# - VLLM_PREBUILT: whether to use prebuilt wheel (1/0)
# - VLLM_USE_PRECOMPILED: whether to use precompiled binaries (1/0)
# - VLLM_PRECOMPILED_WHEEL_COMMIT: commit SHA for precompiled wheel lookup (defaults to VLLM_COMMIT_SHA)
# - CUDA_MAJOR: The major CUDA version
# - BUILD_NIXL_FROM_SOURCE: if nixl should be installed by vLLM or has been built from source in the builder stages

. /opt/vllm/bin/activate

# default VLLM_PRECOMPILED_WHEEL_COMMIT to VLLM_COMMIT_SHA if not set
VLLM_PRECOMPILED_WHEEL_COMMIT="${VLLM_PRECOMPILED_WHEEL_COMMIT:-${VLLM_COMMIT_SHA}}"

# build list of packages to install
# flashinfer-cubin/jit-cache are pre-built wheels (building from source times out)
FLASHINFER_WHEEL_VERSION="${FLASHINFER_VERSION#v}"
INSTALL_PACKAGES=(
  cuda-python
  'huggingface_hub[hf_xet]'
  flashinfer-cubin=="${FLASHINFER_WHEEL_VERSION}"
  flashinfer-jit-cache=="${FLASHINFER_WHEEL_VERSION}"
  /tmp/wheels/*.whl
)
if [ "${BUILD_NIXL_FROM_SOURCE}" = "false" ]; then
  INSTALL_PACKAGES+=(nixl)
fi

# clone vllm repository
git clone "${VLLM_REPO}" /opt/vllm-source
git -C /opt/vllm-source config --system --add safe.directory /opt/vllm-source
git -C /opt/vllm-source fetch --depth=1 origin "${VLLM_COMMIT_SHA}" || true
git -C /opt/vllm-source checkout -q "${VLLM_COMMIT_SHA}"

# detect if prebuilt wheel exists (using VLLM_PRECOMPILED_WHEEL_COMMIT for lookup)
# note: vllm wheel index structure isn't pip-compatible, so we scrape the HTML directly
echo "DEBUG: Looking for wheel at: https://wheels.vllm.ai/${VLLM_PRECOMPILED_WHEEL_COMMIT}/vllm/"
echo "DEBUG: Architecture: $(uname -m), Python: $(python3 --version)"

# determine platform tag from architecture
MACHINE=$(uname -m)
case "${MACHINE}" in
  x86_64) PLATFORM_TAG="manylinux_2_31_x86_64" ;;
  amd64) PLATFORM_TAG="manylinux_2_31_x86_64" ;;
  aarch64) PLATFORM_TAG="manylinux_2_31_aarch64" ;;
  arm64) PLATFORM_TAG="manylinux_2_31_aarch64" ;;
  *) echo "unsupported architecture: ${MACHINE}"; exit 1 ;;
esac

# scrape wheel filename from HTML index
WHEEL_INDEX_HTML=$(curl -sf "https://wheels.vllm.ai/${VLLM_PRECOMPILED_WHEEL_COMMIT}/vllm/" || echo "")
if [ -z "${WHEEL_INDEX_HTML}" ]; then
  echo "DEBUG: Failed to fetch wheel index or index does not exist"
  WHEEL_FILENAME=""
else
  WHEEL_FILENAME=$(echo "${WHEEL_INDEX_HTML}" | grep -oE "vllm-[^\"]+${PLATFORM_TAG}\.whl" | head -1)
fi

if [ -n "${WHEEL_FILENAME}" ]; then
  # construct full URL (wheels are in parent directory)
  # URL-encode the + sign in the wheel filename
  WHEEL_URL="https://wheels.vllm.ai/${VLLM_PRECOMPILED_WHEEL_COMMIT}/${WHEEL_FILENAME}"
  WHEEL_URL=$(echo "${WHEEL_URL}" | sed -E 's/\+/%2B/g')
  echo "DEBUG: Found wheel: ${WHEEL_FILENAME}"
  echo "DEBUG: Wheel URL: ${WHEEL_URL}"
else
  WHEEL_URL=""
  echo "DEBUG: No wheel found for platform: ${PLATFORM_TAG}"
fi

if [ "${VLLM_PREBUILT}" = "1" ]; then
  if [ -z "${WHEEL_URL}" ]; then
    echo "VLLM_PREBUILT set but no platform compatible wheel exists for: https://wheels.vllm.ai/${VLLM_PRECOMPILED_WHEEL_COMMIT}/vllm/"
    exit 1
  fi
  INSTALL_PACKAGES+=("${WHEEL_URL}")
  rm /opt/warn-vllm-precompiled.sh
else
  if [ "${VLLM_USE_PRECOMPILED}" = "1" ] && [ -n "${WHEEL_URL}" ]; then
    echo "Using precompiled binaries and shared libraries from commit: ${VLLM_PRECOMPILED_WHEEL_COMMIT} (source: ${VLLM_COMMIT_SHA})."
    export VLLM_USE_PRECOMPILED=1
    export VLLM_PRECOMPILED_WHEEL_LOCATION="${WHEEL_URL}"
    INSTALL_PACKAGES+=(-e /opt/vllm-source)
    /opt/warn-vllm-precompiled.sh
    rm /opt/warn-vllm-precompiled.sh
  else
    echo "Compiling fully from source. Either precompile disabled or wheel not found in index from main."
    unset VLLM_USE_PRECOMPILED VLLM_PRECOMPILED_WHEEL_LOCATION || true
    INSTALL_PACKAGES+=(-e /opt/vllm-source)
    rm /opt/warn-vllm-precompiled.sh
  fi
fi

# debug: print desired package list
echo "DEBUG: Installing packages: ${INSTALL_PACKAGES[*]}"

# install all packages in one command; enable verbose output by default to help prevent GHA timeouts (can be suppressed via SUPPRESS_PYTHON_OUTPUT)
# use flashinfer wheel index for jit-cache pre-built binaries
CUDA_SHORT_VERSION="cu${CUDA_MAJOR}${CUDA_MINOR}"
VERBOSE_FLAG="-v"
if [ "${SUPPRESS_PYTHON_OUTPUT}" = "true" ] || [ "${SUPPRESS_PYTHON_OUTPUT}" = "1" ]; then
  VERBOSE_FLAG=""
fi
uv pip install ${VERBOSE_FLAG} "${INSTALL_PACKAGES[@]}" \
  --extra-index-url "https://flashinfer.ai/whl/${CUDA_SHORT_VERSION}"

# uninstall the NVSHMEM dependency brought in by vllm if using a compiled NVSHMEM
if [[ "${NVSHMEM_DIR-}" != "" ]]; then
  uv pip uninstall nvidia-nvshmem-cu${CUDA_MAJOR}
fi

# cleanup
rm -rf /tmp/wheels
