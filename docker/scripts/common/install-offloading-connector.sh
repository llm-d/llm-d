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

: "${TARGETPLATFORM:=linux/amd64}"
: "${WHEELS_DIR:=/tmp/wheels}"
: "${GITHUB_REPO:=llm-d/llm-d-kv-cache}"
: "${GITHUB_REF:=main}"
: "${LLM_D_OFFLOADING_CONNECTOR_VERSION:=}"

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

# Find the matching wheel for the target architecture
if [ -n "${LLM_D_OFFLOADING_CONNECTOR_VERSION}" ]; then
    # Use a specific version
    wheel="$(find "${wheels_src}" -name "llmd_fs_connector-${LLM_D_OFFLOADING_CONNECTOR_VERSION}-*${arch}*.whl" | head -n 1)"
else
    # Use the latest version (sorted by version number, last entry)
    wheel="$(find "${wheels_src}" -name "*${arch}*.whl" | sort -V | tail -n 1)"
fi

if [ -z "${wheel}" ]; then
    echo "No matching wheel found for arch=${arch} in ${wheels_src}" >&2
    ls -la "${wheels_src}" >&2
    exit 1
fi

cp "${wheel}" "${WHEELS_DIR}/"
ls -lah "${WHEELS_DIR}/$(basename "${wheel}")"

# Clean up the cloned repo
rm -rf "${clone_dir}"
