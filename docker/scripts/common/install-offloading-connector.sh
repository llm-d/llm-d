#!/bin/bash
set -Eeuox pipefail

# downloads the llm-d KV-cache offloading connector wheel into /tmp/wheels
# (to be installed later by a single `uv` install step)
#
# Required environment variables:
# - INSTALL_FROM_LATEST_RELEASE: true/false
# - TARGETPLATFORM: platform target (linux/arm64 or linux/amd64)
# Optional environment variables:
# - WHEEL_URL: direct URL to a .whl (e.g. CI artifact). Used when INSTALL_FROM_LATEST_RELEASE=false.
# - WHEELS_DIR: destination directory for wheels (default: /tmp/wheels)
# - GITHUB_REPO: repo to pull releases from (default: llm-d/llm-d-kv-cache)
# - GITHUB_TOKEN: token for GitHub API/auth (optional; helps with rate limits)

: "${TARGETPLATFORM:=linux/amd64}"
: "${INSTALL_FROM_LATEST_RELEASE:=true}"
: "${WHEEL_URL:=}"
: "${WHEELS_DIR:=/tmp/wheels}"
: "${GITHUB_REPO:=llm-d/llm-d-kv-cache}"

mkdir -p "${WHEELS_DIR}"
cd /tmp

mkdir -p "${WHEELS_DIR}"
cd /tmp

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

fetch_latest_release_wheel_url() {
    local arch="$1"

    curl -fsSL "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
    | jq -r --arg arch "${arch}" '
        .assets[]
        | select(.name | endswith(".whl"))
        | select(.name | contains($arch))
        | .browser_download_url
    ' \
    | head -n 1
}

arch="$(platform_to_wheel_arch)"

if [ "${INSTALL_FROM_LATEST_RELEASE}" = "true" ]; then
    wheel_url="$(fetch_latest_release_wheel_url "${arch}")"

    if [ -z "${wheel_url}" ]; then
        echo "No matching wheel found in latest release for arch=${arch}" >&2
        exit 1
    fi

    wheel_name="$(basename "${wheel_url%%\?*}")"
    out="${WHEELS_DIR}/${wheel_name}"

    curl -fL --retry 5 --retry-delay 1 \
        -o "${out}" \
        "${wheel_url}"

    ls -lah "${out}"
    exit 0
fi

# Not installing from latest release: use direct URL (e.g. CI artifact)
if [ -z "${WHEEL_URL}" ]; then
    echo "INSTALL_FROM_LATEST_RELEASE=false and WHEEL_URL is empty; skipping download." >&2
    exit 0
fi

wheel_name="$(basename "${WHEEL_URL%%\?*}")"
out="${WHEELS_DIR}/${wheel_name}"

curl -fL --retry 5 --retry-delay 1 \
    -o "${out}" \
    "${WHEEL_URL}"

ls -lah "${out}"