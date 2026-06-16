#!/bin/bash
set -Eeux

# builds and installs UCCL from source
#
# Required environment variables:
# - UCCL_REPO: git repo to build UCCL from
# - UCCL_VERSION: git ref to build UCCL from
# - UCCL_PREFIX: installation prefix for UCCL libraries and headers
#
# Optional environment variables:
# - UCCL_DEVICE: device target (default: cuda). Options: cuda, rocm

UCCL_DEVICE="${UCCL_DEVICE:-cuda}"

cd /tmp

git clone "${UCCL_REPO}" uccl && cd uccl
git checkout -q "${UCCL_VERSION}"

mkdir -p "${UCCL_PREFIX}/lib" "${UCCL_PREFIX}/include"

# Install pre-requisites
if [ "${TARGETOS:-rhel}" = "ubuntu" ]; then
    apt-get update && apt-get install -y --no-install-recommends libelf-dev
else
    dnf install -y elfutils-libelf-devel
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
    uv pip install nanobind
else
    uv pip install --system nanobind
fi

cd p2p

# Select Makefile based on device
if [ "${UCCL_DEVICE}" = "rocm" ]; then
    MAKEFILE="Makefile.rocm"
else
    MAKEFILE="Makefile"
fi

PREFIX="${UCCL_PREFIX}" make -f "${MAKEFILE}" install

cd /tmp
rm -rf uccl
