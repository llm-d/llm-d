#!/bin/bash
set -Eeu

# installs base packages, EPEL, and CUDA repository
#
# Required environment variables:
# - PYTHON_VERSION: Python version to install (e.g., 3.12)

dnf -q install -y dnf-plugins-core
dnf -q install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
dnf config-manager --set-enabled epel

DOWNLOAD_ARCH=""
if [ "$(uname -m)" = "amd64" ] || [ "$(uname -m)" = "x86_64" ]; then
    DOWNLOAD_ARCH="x86_64"
elif [ "$(uname -m)" = "aarch64" ]; then
    DOWNLOAD_ARCH="aarch64"
fi

dnf config-manager --add-repo "https://developer.download.nvidia.com/compute/cuda/repos/rhel9/${DOWNLOAD_ARCH}/cuda-rhel9.repo"

dnf -q install -y --allowerasing \
    "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-pip" "python${PYTHON_VERSION}-wheel" \
    "python${PYTHON_VERSION}-devel" \
    python3.9-devel \
    which procps findutils tar \
    gcc gcc-c++ \
    make cmake \
    autoconf automake libtool \
    git \
    curl wget \
    gzip \
    zlib-devel \
    openssl-devel \
    pkg-config \
    libuuid-devel \
    glibc-devel \
    rdma-core-devel \
    libibverbs \
    libibverbs-devel \
    numactl-libs \
    subunit \
    pciutils \
    pciutils-libs \
    ninja-build \
    xz \
    rsync

dnf clean all
