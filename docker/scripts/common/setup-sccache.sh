#!/bin/bash
# sets up sccache environment variables and starts the server
#
# Required environment variables:
# - USE_SCCACHE: whether to configure and start sccache (true/false)
# - TARGETPLATFORM: target platform for build (defaults to linux/amd64)

if [ "${USE_SCCACHE}" = "true" ]; then
    # set up AWS credentials if secrets are available
    if [ -f "/run/secrets/aws_access_key_id" ] && [ -f "/run/secrets/aws_secret_access_key" ]; then
        AWS_ACCESS_KEY_ID="$(cat /run/secrets/aws_access_key_id)"
        export AWS_ACCESS_KEY_ID
        AWS_SECRET_ACCESS_KEY="$(cat /run/secrets/aws_secret_access_key)"
        export AWS_SECRET_ACCESS_KEY
        export AWS_DEFAULT_REGION="us-west-2"
        export AWS_EC2_METADATA_DISABLED=true
    fi

    export CMAKE_C_COMPILER_LAUNCHER=sccache
    export CMAKE_CXX_COMPILER_LAUNCHER=sccache
    export CMAKE_CUDA_COMPILER_LAUNCHER=sccache

    # configure sccache via environment variables
    export SCCACHE_BUCKET="vllm-nightly-sccache"
    export SCCACHE_REGION="us-west-2"
    export SCCACHE_S3_KEY_PREFIX="llm-d-cache/"
    export SCCACHE_IDLE_TIMEOUT=0

    # use platform-specific unix socket to avoid port conflicts in multi-platform builds
    case "${TARGETPLATFORM:-linux/amd64}" in
        linux/arm64) export SCCACHE_SERVER_UDS="/tmp/sccache-arm64.sock" ;;
        linux/amd64) export SCCACHE_SERVER_UDS="/tmp/sccache-amd64.sock" ;;
        *) export SCCACHE_SERVER_UDS="/tmp/sccache.sock" ;;
    esac

    if ! /usr/local/bin/sccache --start-server; then
        echo "Warning: sccache failed to start, continuing without cache" >&2
        # Remove sccache binary so meson/cmake can't accidentally try to use it
        rm -f /usr/local/bin/sccache || true
        unset CMAKE_C_COMPILER_LAUNCHER CMAKE_CXX_COMPILER_LAUNCHER CMAKE_CUDA_COMPILER_LAUNCHER
        return 0
    fi

    if ! /usr/local/bin/sccache --show-stats >/dev/null 2>&1; then
        echo "Warning: sccache not responding properly, disabling cache" >&2
        /usr/local/bin/sccache --stop-server 2>/dev/null || true
        # Remove sccache binary so meson/cmake can't accidentally try to use it
        rm -f /usr/local/bin/sccache || true
        unset CMAKE_C_COMPILER_LAUNCHER CMAKE_CXX_COMPILER_LAUNCHER CMAKE_CUDA_COMPILER_LAUNCHER
        return 0
    fi

    echo "sccache successfully configured:"
    echo "  - Bucket: ${SCCACHE_BUCKET}"
    echo "  - Region: ${SCCACHE_REGION}"
    echo "  - Key prefix: ${SCCACHE_S3_KEY_PREFIX}"
    echo "  - Socket: ${SCCACHE_SERVER_UDS}"
    echo "  - AWS credentials: $([ -n \"${AWS_ACCESS_KEY_ID:-}\" ] && echo 'set' || echo 'NOT SET')"
    /usr/local/bin/sccache --show-stats 2>&1 | head -5
fi
