ARG BASE_IMAGE="nvcr.io/nvidia/cuda"
ARG BASE_IMAGE_TAG="12.8.1-devel-ubuntu24.04"

ARG RUNTIME_IMAGE="nvcr.io/nvidia/cuda"
ARG RUNTIME_IMAGE_TAG="12.8.1-devel-ubuntu24.04"

FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG} AS nixl_base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && apt-get install git -y

# ##################################
# ########## Build Image ###########
# ##################################

FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG} AS build
ENV DEBIAN_FRONTEND=noninteractive

USER root

### NIXL Dependencies setup ###

ARG MOFED_VERSION=24.10-1.1.4.0
ARG PYTHON_VERSION=3.12
ARG NSYS_URL=https://developer.nvidia.com/downloads/assets/tools/secure/nsight-systems/2025_1/
ARG NSYS_PKG=NsightSystems-linux-cli-public-2025.1.1.131-3554042.deb

RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update -y && apt-get install -y --no-install-recommends \
    curl \
    git \
    libnuma-dev \
    numactl \
    wget \
    autotools-dev \
    automake \
    libtool \
    libz-dev \
    libiberty-dev \
    flex \
    build-essential \
    cmake \
    libibverbs-dev \
    libgoogle-glog-dev \
    libgtest-dev \
    libjsoncpp-dev \
    libpython3-dev \
    libboost-all-dev \
    libssl-dev \
    libgrpc-dev \
    libgrpc++-dev \
    libprotobuf-dev \
    libclang-dev \
    protobuf-compiler-grpc \
    pybind11-dev \
    python3-full \
    python3-pip \
    python3-numpy \
    etcd-server \
    net-tools \
    pciutils \
    libpci-dev \
    vim \
    tmux \
    screen \
    ibverbs-utils \
    libibmad-dev \
    linux-tools-common \
    linux-tools-generic \
    ethtool \
    iproute2 \
    dkms \
    linux-headers-generic \
    meson \
    ninja-build \
    uuid-dev \
    gdb \
    libglib2.0-0 \
    libibverbs1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN wget ${NSYS_URL}${NSYS_PKG} &&\
    apt install -y ./${NSYS_PKG} &&\
    rm ${NSYS_PKG}

RUN cd /usr/local/src && \
    curl -fSsL "https://content.mellanox.com/ofed/MLNX_OFED-${MOFED_VERSION}/MLNX_OFED_LINUX-${MOFED_VERSION}-ubuntu24.04-x86_64.tgz" -o mofed.tgz && \
    tar -xf /usr/local/src/mofed.tgz && \
    cd MLNX_OFED_LINUX-* && \
    apt-get update && apt-get install -y --no-install-recommends \
    ./DEBS/libibverbs* ./DEBS/ibverbs-providers* ./DEBS/librdmacm* ./DEBS/libibumad* && \
    rm -rf /var/lib/apt/lists/* /usr/local/src/* mofed.tgz

ENV LIBRARY_PATH=$LIBRARY_PATH:/usr/local/cuda/lib64 \
    LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/cuda/lib64

ENV LIBRARY_PATH=$LIBRARY_PATH:/usr/local/lib \
    LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/lib

WORKDIR /workspace
RUN git clone https://github.com/NVIDIA/gdrcopy.git
RUN PREFIX=/usr/local DESTLIB=/usr/local/lib make -C /workspace/gdrcopy lib_install
RUN cp gdrcopy/src/libgdrapi.so.2.* /usr/lib/x86_64-linux-gnu/
RUN ldconfig

ARG UCX_VERSION=v1.18.0

RUN cd /usr/local/src && \
    curl -fSsL "https://github.com/openucx/ucx/tarball/${UCX_VERSION}" | tar xz && \
    cd openucx-ucx* && \
    ./autogen.sh && ./configure     \
    --enable-shared             \
    --disable-static            \
    --disable-doxygen-doc       \
    --enable-optimizations      \
    --enable-cma                \
    --enable-devel-headers      \
    --with-cuda=/usr/local/cuda \
    --with-verbs                \
    --with-dm                   \
    --with-gdrcopy=/usr/local   \
    --enable-mt                 \
    --with-mlx5-dv &&           \
    make -j &&                      \
    make -j install-strip &&        \
    ldconfig

ENV LD_LIBRARY_PATH=/usr/lib:$LD_LIBRARY_PATH
ENV CPATH=/usr/include:$CPATH
ENV PATH=/usr/bin:$PATH
ENV PKG_CONFIG_PATH=/usr/lib/pkgconfig:$PKG_CONFIG_PATH
SHELL ["/bin/bash", "-c"]

WORKDIR /workspace

ENV LD_LIBRARY_PATH=/usr/local/ompi/lib:$LD_LIBRARY_PATH
ENV CPATH=/usr/local/ompi/include:$CPATH
ENV PATH=/usr/local/ompi/bin:$PATH
ENV PKG_CONFIG_PATH=/usr/local/ompi/lib/pkgconfig:$PKG_CONFIG_PATH

### NIXL setup ###

WORKDIR /opt
RUN mkdir -p /opt/nixl
WORKDIR /opt/nixl

ARG NIXL_VERSION="0.1.1"

RUN wget "https://github.com/ai-dynamo/nixl/archive/refs/tags/${NIXL_VERSION}.tar.gz"
RUN tar --strip-components=1 -zxvf ${NIXL_VERSION}.tar.gz && rm ${NIXL_VERSION}.tar.gz

RUN mkdir build && \
    meson setup build/ --prefix=/usr/local/nixl && \
    cd build && \
    ninja && \
    ninja install

ENV LD_LIBRARY_PATH=/usr/local/nixl/lib/x86_64-linux-gnu/:$LD_LIBRARY_PATH
ENV PYTHONPATH=/usr/local/nixl/lib/python3/dist-packages/:/opt/nixl/test/python/:$PYTHONPATH
ENV NIXL_PLUGIN_DIR=/usr/local/nixl/lib/x86_64-linux-gnu/plugins

# ✅ Ensure uv is in PATH BEFORE trying to use it
ENV PATH="/root/.local/bin:$PATH"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# TMPDIR override to avoid /tmp overuse
ENV TMPDIR=/workspace/tmp
RUN mkdir -p /workspace/tmp

# Clean up unnecessary files to free up space
RUN rm -rf /usr/local/src/* /opt/nixl/build /workspace/gdrcopy /root/.cache /tmp/* /var/tmp/*

# Git clone repos

# Env to force rebuilding all layers below
ENV LMCACHE_COMMIT_SHA=c1563bc9c72ea0d71156a3d9a6cd643170828acf
ENV VLLM_COMMIT_SHA=db9a82dd8b7b85af3d0a6956bae92e1a5d986413

WORKDIR /workspace
RUN git clone https://github.com/neuralmagic/LMCache.git && \
    cd LMCache && \
    git checkout -q $LMCACHE_COMMIT_SHA && \
    cd ..

RUN git clone -b pd-launch-branch https://github.com/neuralmagic/vllm.git && \
    cd vllm && \
    git checkout -q $VLLM_COMMIT_SHA && \
    cd ..

# Set up Python virtual environment with uv
WORKDIR /workspace/vllm
RUN uv venv .vllm --python 3.12

# Supported archs
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;10.0;12.0"

# Install core dependencies (Torch first)
RUN . .vllm/bin/activate && \
    uv pip install --upgrade pip && \
    uv pip install torch==2.7.0 --torch-backend=cu128

# Install vllm editable
RUN . .vllm/bin/activate && \
    uv pip install --editable . --torch-backend=cu128

# Install related packages and cleanup
RUN . .vllm/bin/activate && \
    uv pip install ../LMCache/ && \
    uv pip install nixl && \
    uv cache clean && \
    rm -rf .git ../LMCache

# Final environment setup
ENV PATH="/workspace/vllm/.vllm/bin:${PATH}"

ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
