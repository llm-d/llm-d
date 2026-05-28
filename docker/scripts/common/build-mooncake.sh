#!/bin/bash
set -Eeux

# Builds and installs Mooncake (distributed KV cache) from source.
#
# Required environment variables:
# - MOONCAKE_VERSION: git tag/branch/SHA to checkout
# - MOONCAKE_TARGET_ACCELERATOR: target accelerator, one of:
#     cuda    - NVIDIA GPU (default cmake, auto-detects CUDA toolkit)
#     ascend  - Huawei Ascend NPU (enables ASCEND_DIRECT transport)
#     cpu     - CPU-only (no accelerator-specific transport)
#
# Optional environment variables:
# - MOONCAKE_REPO: git repo URL (default: https://github.com/kvcache-ai/Mooncake.git)
# - MOONCAKE_CMAKE_EXTRA_FLAGS: additional cmake flags appended after the accelerator defaults

: "${MOONCAKE_REPO:=https://github.com/kvcache-ai/Mooncake.git}"
: "${MOONCAKE_TARGET_ACCELERATOR:=cuda}"

cd /tmp

git clone --depth 1 --branch "${MOONCAKE_VERSION}" "${MOONCAKE_REPO}" mooncake
cd mooncake

bash dependencies.sh -y

# Set up accelerator-specific cmake flags and environment
CMAKE_FLAGS=""
case "${MOONCAKE_TARGET_ACCELERATOR}" in
    cuda)
        ;;
    ascend)
        CMAKE_FLAGS="-DUSE_ASCEND_DIRECT=ON"
        if [ -n "${ASCEND_TOOLKIT_HOME:-}" ]; then
            ARCH=$(uname -m)
            # shellcheck disable=SC1091
            source "${ASCEND_TOOLKIT_HOME}/../set_env.sh" || source /usr/local/Ascend/ascend-toolkit/set_env.sh
            export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/${ARCH}-linux/devlib:/usr/local/Ascend/ascend-toolkit/latest/${ARCH}-linux/lib64:${LD_LIBRARY_PATH:-}"
        fi
        ;;
    cpu)
        ;;
    *)
        echo "ERROR: unknown MOONCAKE_TARGET_ACCELERATOR='${MOONCAKE_TARGET_ACCELERATOR}'" >&2
        echo "Valid values: cuda, ascend, cpu" >&2
        exit 1
        ;;
esac

mkdir -p build && cd build
# shellcheck disable=SC2086
cmake .. ${CMAKE_FLAGS} ${MOONCAKE_CMAKE_EXTRA_FLAGS:-}
make -j"$(nproc)"
make install

cd /tmp && rm -rf /tmp/mooncake
