#!/usr/bin/env bash
#
# Build the llm-d CUDA image locally.
#
# Usage:
#   ./docker/scripts/cuda/builder/build-local-llm-d-cuda.sh                # defaults
#   ./docker/scripts/cuda/builder/build-local-llm-d-cuda.sh --tag my-test   # custom tag
#   ./docker/scripts/cuda/builder/build-local-llm-d-cuda.sh --help
#
# All options can also be set via environment variables (see below).
#
set -euo pipefail

# ── repo root ────────────────────────────────────────────────────────
REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

# ── configurable defaults (override via env or flags) ────────────────
IMAGE_NAME="${IMAGE_NAME:-llm-d-cuda}"
IMAGE_TAG="${IMAGE_TAG:-local}"
TARGET="${TARGET:-}"                       # "" = full image, "builder" = builder only
TARGETPLATFORM="${TARGETPLATFORM:-linux/amd64}"
BUILD_DEBUG="${BUILD_DEBUG:-false}"
ENABLE_EFA="${ENABLE_EFA:-false}"
TARGETOS="${TARGETOS:-rhel}"
DOCKER="${DOCKER:-docker}"                 # or podman
BUILDER="${BUILDER:-}"                     # buildx builder name (e.g. remote-amd64)

# ── parse flags ──────────────────────────────────────────────────────
usage() {
    cat <<EOF
Build the llm-d CUDA image locally.

Usage: $(basename "$0") [OPTIONS]

Options:
  --image-name NAME   Image name                  (default: ${IMAGE_NAME})
  --tag TAG           Image tag                   (default: ${IMAGE_TAG})
  --target STAGE      Dockerfile target stage      (default: full image)
                      Accepted values: builder, runtime
  --platform PLAT     Target platform              (default: ${TARGETPLATFORM})
                      e.g. linux/amd64, linux/arm64
  --debug             Build with debug symbols     (default: false)
  --efa               Enable AWS EFA support       (default: false)
  --os OS             Target OS: rhel or ubuntu    (default: ${TARGETOS})
  --docker CMD        Container tool               (default: ${DOCKER})
  --builder NAME      Buildx builder name          (default: default builder)
                      e.g. remote-amd64
  --dry-run           Print the docker command without running it
  -h, --help          Show this help message

Environment variables:
  IMAGE_NAME, IMAGE_TAG, TARGET, TARGETPLATFORM, BUILD_DEBUG,
  ENABLE_EFA, TARGETOS, DOCKER, BUILDER
EOF
    exit 0
}

DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image-name) IMAGE_NAME="$2"; shift 2 ;;
        --tag)        IMAGE_TAG="$2";  shift 2 ;;
        --target)     TARGET="$2";     shift 2 ;;
        --platform)   TARGETPLATFORM="$2"; shift 2 ;;
        --debug)      BUILD_DEBUG=true; shift ;;
        --efa)        ENABLE_EFA=true;  shift ;;
        --os)         TARGETOS="$2";   shift 2 ;;
        --docker)     DOCKER="$2";     shift 2 ;;
        --builder)    BUILDER="$2";    shift 2 ;;
        --dry-run)    DRY_RUN=true;    shift ;;
        -h|--help)    usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# ── resolve OS-specific image suffixes ───────────────────────────────
case "${TARGETOS}" in
    rhel)
        BUILD_BASE_IMAGE_SUFFIX="ubi9"
        FINAL_BASE_IMAGE_SUFFIX="ubi9"
        ;;
    ubuntu)
        BUILD_BASE_IMAGE_SUFFIX="ubuntu20.04"
        FINAL_BASE_IMAGE_SUFFIX="ubuntu24.04"
        ;;
    *)
        echo "Error: unsupported --os value '${TARGETOS}'. Use 'rhel' or 'ubuntu'." >&2
        exit 1
        ;;
esac

# ── load vLLM version pinning ───────────────────────────────────────
VLLM_VERSION_FILE="${REPO_ROOT}/docker/vllm-version"
if [[ ! -f "${VLLM_VERSION_FILE}" ]]; then
    echo "Error: ${VLLM_VERSION_FILE} not found." >&2
    exit 1
fi
# shellcheck source=/dev/null
source "${VLLM_VERSION_FILE}"

# ── check container tool ────────────────────────────────────────────
if ! command -v "${DOCKER}" &>/dev/null; then
    echo "Error: '${DOCKER}' not found. Install Docker or set --docker." >&2
    exit 1
fi

# ── build the command ────────────────────────────────────────────────
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

cmd=(
    "${DOCKER}" buildx build
    ${BUILDER:+--builder "${BUILDER}"}
    --load
    --progress=plain
    --platform "${TARGETPLATFORM}"
    -f "${REPO_ROOT}/docker/Dockerfile.cuda"
    --build-arg "TARGETPLATFORM=${TARGETPLATFORM}"
    --build-arg "TARGETOS=${TARGETOS}"
    --build-arg "BUILD_BASE_IMAGE_SUFFIX=${BUILD_BASE_IMAGE_SUFFIX}"
    --build-arg "FINAL_BASE_IMAGE_SUFFIX=${FINAL_BASE_IMAGE_SUFFIX}"
    --build-arg "BUILD_DEBUG=${BUILD_DEBUG}"
    --build-arg "ENABLE_EFA=${ENABLE_EFA}"
    --build-arg "VLLM_REPO=${VLLM_REPO}"
    --build-arg "VLLM_COMMIT_SHA=${VLLM_COMMIT_SHA}"
    --build-arg "VLLM_PRECOMPILED_WHEEL_COMMIT=${VLLM_PRECOMPILED_WHEEL_COMMIT:-${VLLM_COMMIT_SHA}}"
    --build-arg "VLLM_PREBUILT=${VLLM_PREBUILT:-0}"
    --build-arg "VLLM_USE_PRECOMPILED=${VLLM_USE_PRECOMPILED:-1}"
)

if [[ -n "${TARGET}" ]]; then
    cmd+=(--target "${TARGET}")
fi

cmd+=(-t "${FULL_IMAGE}" "${REPO_ROOT}")

# ── run ──────────────────────────────────────────────────────────────
echo "==> Building ${FULL_IMAGE}"
echo "    Platform=${TARGETPLATFORM}  OS=${TARGETOS}  DEBUG=${BUILD_DEBUG}  EFA=${ENABLE_EFA}  Builder=${BUILDER:-default}"
echo "    vLLM repo=${VLLM_REPO}"
echo "    vLLM commit=${VLLM_COMMIT_SHA}"
echo ""

if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[dry-run] ${cmd[*]}"
    exit 0
fi

exec "${cmd[@]}"
