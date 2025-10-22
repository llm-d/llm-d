#!/bin/bash
set -Eeu

# installs vllm and dependencies in runtime stage
#
# Required environment variables:
# - VLLM_REPO: vLLM git repository URL
# - VLLM_COMMIT_SHA: vLLM commit SHA to checkout
# - VLLM_PREBUILT: whether to use prebuilt wheel (1/0)
# - VLLM_USE_PRECOMPILED: whether to use precompiled binaries (1/0)
# - VLLM_PRECOMPILED_WHEEL_COMMIT: commit SHA for precompiled wheel lookup (defaults to VLLM_COMMIT_SHA)

# shellcheck source=/dev/null
source /opt/vllm/bin/activate

# default VLLM_PRECOMPILED_WHEEL_COMMIT to VLLM_COMMIT_SHA if not set
VLLM_PRECOMPILED_WHEEL_COMMIT="${VLLM_PRECOMPILED_WHEEL_COMMIT:-${VLLM_COMMIT_SHA}}"

# build list of packages to install
INSTALL_PACKAGES=(
  nixl
  cuda-python
  'huggingface_hub[hf_xet]'
  /tmp/wheels/*.whl
)

# clone vllm repository
git clone "${VLLM_REPO}" /opt/vllm-source
git -C /opt/vllm-source config --system --add safe.directory /opt/vllm-source
git -C /opt/vllm-source fetch --depth=1 origin "${VLLM_COMMIT_SHA}" || true
git -C /opt/vllm-source checkout -q "${VLLM_COMMIT_SHA}"

# detect if prebuilt wheel exists (using VLLM_PRECOMPILED_WHEEL_COMMIT for lookup)
WHEEL_URL=$(pip install \
  --no-cache-dir \
  --no-index \
  --no-deps \
  --find-links "https://wheels.vllm.ai/${VLLM_PRECOMPILED_WHEEL_COMMIT}/vllm/" \
  --only-binary=:all: \
  --pre vllm \
  --dry-run \
  --disable-pip-version-check \
  -qqq \
  --report - \
  2>/dev/null | jq -r '.install[0].download_info.url')

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

# install all packages in one command
uv pip install "${INSTALL_PACKAGES[@]}"

# cleanup
rm -rf /tmp/wheels
