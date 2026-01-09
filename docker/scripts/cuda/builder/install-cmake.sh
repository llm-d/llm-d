#!/bin/bash
set -Eeu

# purpose: install cmake 3.22.0 as a workaround for building nvshmem from source
# CMAKE version is locked here because the url changes when downloading from github based on the version

export CMAKE_VERSION="3.22.0"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  CMAKE_ARCH=linux-x86_64 ;;
    amd64)   CMAKE_ARCH=linux-x86_64 ;;
    aarch64) CMAKE_ARCH=linux-aarch64 ;;
    arm64)   CMAKE_ARCH=linux-aarch64 ;;
    *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# Install to /usr/local without prompts
curl -fsSL "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-${CMAKE_ARCH}.sh" \
-o /tmp/cmake.sh
chmod +x /tmp/cmake.sh
/tmp/cmake.sh --skip-license --prefix=/usr/local --exclude-subdir
