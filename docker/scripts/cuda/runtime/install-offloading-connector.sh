#!/bin/bash
set -Eeuox pipefail

# Clones the llm-d-kv-cache repo and copies the offloading connector wheel
# into /tmp/wheels (to be installed later by a single `uv` install step)
#
# Required environment variables:
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)
# Optional environment variables:
# - WHEELS_DIR: destination directory for wheels (default: /tmp/wheels)
# - GITHUB_REPO: repo to clone (default: llm-d/llm-d-kv-cache)
# - GITHUB_REF: branch/tag/commit to checkout (default: main)
# - LLM_D_OFFLOADING_CONNECTOR_VERSION: specific wheel version to use (default: latest available)
# - CUDA_MAJOR: CUDA major version (e.g., 13). When >= 13, selects the +cu{MAJOR}{MINOR} wheel variant.
# - CUDA_MINOR: CUDA minor version (e.g., 0). Used with CUDA_MAJOR for variant suffix.

: "${TARGETPLATFORM:=linux/amd64}"
: "${WHEELS_DIR:=/tmp/wheels}"
: "${GITHUB_REPO:=llm-d/llm-d-kv-cache}"
: "${GITHUB_REF:=main}"
: "${LLM_D_OFFLOADING_CONNECTOR_VERSION:=}"
: "${CUDA_MAJOR:=}"
: "${CUDA_MINOR:=}"

mkdir -p "${WHEELS_DIR}"

platform_to_wheel_arch() {
    case "${TARGETPLATFORM}" in
        linux/amd64) echo "x86_64" ;;
        linux/arm64) echo "aarch64" ;;
        *)
            echo "Unsupported TARGETPLATFORM='${TARGETPLATFORM}'" >&2
            exit 1
            ;;
    esac
}

arch="$(platform_to_wheel_arch)"

# Shallow clone the repo
clone_dir="/tmp/llm-d-kv-cache"
rm -rf "${clone_dir}"
git clone --depth 1 --branch "${GITHUB_REF}" "https://github.com/${GITHUB_REPO}.git" "${clone_dir}"

wheels_src="${clone_dir}/kv_connectors/llmd_fs_backend/wheels"

if [ ! -d "${wheels_src}" ]; then
    echo "Wheels directory not found at ${wheels_src}" >&2
    exit 1
fi

# Determine CUDA variant suffix (e.g., "+cu130" for CUDA 13.0)
# Wheels for CUDA >= 13 use a +cu{MAJOR}{MINOR} suffix in the version field
cuda_variant=""
if [ -n "${CUDA_MAJOR}" ] && [ "${CUDA_MAJOR}" -ge 13 ] 2>/dev/null; then
    cuda_variant="+cu${CUDA_MAJOR}${CUDA_MINOR}"
    echo "CUDA ${CUDA_MAJOR}.${CUDA_MINOR} detected, looking for ${cuda_variant} wheel variant"
fi

# Find the matching wheel for the target architecture and CUDA variant
if [ -n "${LLM_D_OFFLOADING_CONNECTOR_VERSION}" ]; then
    if [ -n "${cuda_variant}" ]; then
        # Try CUDA-specific variant first (e.g., llmd_fs_connector-0.19+cu130-...)
        wheel="$(find "${wheels_src}" -name "llmd_fs_connector-${LLM_D_OFFLOADING_CONNECTOR_VERSION}${cuda_variant}-*${arch}*.whl" | head -n 1)"
    fi
    if [ -z "${wheel}" ]; then
        # Fall back to the default (non-CUDA-suffixed) wheel
        wheel="$(find "${wheels_src}" -name "llmd_fs_connector-${LLM_D_OFFLOADING_CONNECTOR_VERSION}-*${arch}*.whl" | head -n 1)"
    fi
else
    # Use the latest version (sorted by version number, last entry)
    if [ -n "${cuda_variant}" ]; then
        wheel="$(find "${wheels_src}" -name "*${cuda_variant}*${arch}*.whl" | sort -V | tail -n 1)"
    fi
    if [ -z "${wheel}" ]; then
        wheel="$(find "${wheels_src}" -name "*${arch}*.whl" | sort -V | tail -n 1)"
    fi
fi

if [ -z "${wheel}" ]; then
    echo "WARNING: No matching offloading connector wheel found for arch=${arch}, skipping installation" >&2
    ls -la "${wheels_src}" >&2
    rm -rf "${clone_dir}"
    exit 0
fi

cp "${wheel}" "${WHEELS_DIR}/"
ls -lah "${WHEELS_DIR}/$(basename "${wheel}")"

# Clean up the cloned repo
rm -rf "${clone_dir}"
