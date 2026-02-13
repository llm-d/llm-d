#!/bin/bash
set -Eeux

# installs sccache binary from github releases and verifies connectivity
#
# Required environment variables:
# - USE_SCCACHE: whether to install and configure sccache (true/false)
# - TARGETOS: Target OS - either 'ubuntu' or 'rhel' (default: rhel)

TARGETOS="${TARGETOS:-rhel}"

if [ "${USE_SCCACHE}" = "true" ]; then
    # detect architecture
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then
        SCCACHE_ARCH="x86_64"
    elif [ "$ARCH" = "aarch64" ]; then
        SCCACHE_ARCH="aarch64"
    else
        echo "Unsupported architecture: $ARCH"
        exit 1
    fi

    SCCACHE_VERSION="v0.11.0"
    mkdir -p /tmp/sccache
    cd /tmp/sccache
    curl -sLO https://github.com/mozilla/sccache/releases/download/${SCCACHE_VERSION}/sccache-${SCCACHE_VERSION}-${SCCACHE_ARCH}-unknown-linux-musl.tar.gz
    tar -xf sccache-${SCCACHE_VERSION}-${SCCACHE_ARCH}-unknown-linux-musl.tar.gz
    mv sccache-${SCCACHE_VERSION}-${SCCACHE_ARCH}-unknown-linux-musl/sccache /usr/local/bin/sccache
    cd /tmp
    rm -rf /tmp/sccache

    # shellcheck source=/dev/null
    source /usr/local/bin/setup-sccache

    # verify sccache works with a simple test (only if binary still exists after setup)
    if [ -x /usr/local/bin/sccache ]; then
        echo "int main() { return 0; }" | sccache gcc -x c - -o /dev/null
        echo "sccache installation and S3 connectivity verified"
    fi
fi
