# SGLang nightly plus an NVSHMEM 3.4.5 IBGDA transport rebuilt with the
# llm-d "zero ibv_ah_attr" patch. Stock NVSHMEM 3.3.20 to 3.4.5 passes an
# uninitialized ibv_ah_attr when creating the IBGDA DCT shared objects; on
# RoCE with kernels that validate static_rate this fails with
# "create DCT share err" / "nvshmem setup connections failed", which breaks
# DeepEP low-latency mode across nodes (GKE A3 Ultra). Only the transport
# plugin is replaced, so the DeepEP kernels built against NVSHMEM 3.4.5 keep
# an identical ABI.
# Must match the SGLang image the recipe deploys (the gpu-sglang/nightly
# component); pin it by digest so the rebuilt plugin matches the wheel inside.
ARG BASE_IMAGE=docker.io/lmsysorg/sglang@sha256:5921361fcf358cdde4df1968c941c14157f418613b099ad7f3e5aeed6427ae15
FROM ${BASE_IMAGE}

ARG NVSHMEM_REF=v3.4.5-0
# Build context: this directory plus the patch copied from the repo root:
#   cp ${REPO_ROOT}/patches/nvshmem_zero_ibv_ah_attr_v3.4.5-0.patch . && docker build -t <your-registry>/sglang-roce:<tag> .
COPY nvshmem_zero_ibv_ah_attr_v3.4.5-0.patch /tmp/nvshmem.patch

RUN set -eux; \
    WHEEL_LIB=/opt/sglang/lib/python3.12/site-packages/nvidia/nvshmem/lib; \
    test -f "${WHEEL_LIB}/nvshmem_transport_ibgda.so.3"; \
    pip show nvidia-nvshmem-cu13 | grep -q '^Version: 3.4.5$'; \
    git clone --depth 1 --branch "${NVSHMEM_REF}" https://github.com/NVIDIA/nvshmem.git /tmp/nvshmem; \
    cd /tmp/nvshmem; \
    git apply /tmp/nvshmem.patch; \
    cmake -S . -B build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \
      -DCUDA_HOME=/usr/local/cuda \
      -DCMAKE_CUDA_ARCHITECTURES=90 \
      -DNVSHMEM_PREFIX=/tmp/nvshmem/install \
      -DNVSHMEM_IBGDA_SUPPORT=1 \
      -DNVSHMEM_IBRC_SUPPORT=1 \
      -DNVSHMEM_IBDEVX_SUPPORT=1 \
      -DNVSHMEM_UCX_SUPPORT=0 \
      -DNVSHMEM_LIBFABRIC_SUPPORT=0 \
      -DNVSHMEM_MPI_SUPPORT=0 \
      -DNVSHMEM_SHMEM_SUPPORT=0 \
      -DNVSHMEM_PMIX_SUPPORT=0 \
      -DNVSHMEM_USE_NCCL=0 \
      -DNVSHMEM_USE_GDRCOPY=0 \
      -DNVSHMEM_BUILD_TESTS=0 \
      -DNVSHMEM_BUILD_EXAMPLES=0 \
      -DNVSHMEM_BUILD_PACKAGES=0 \
      -DNVSHMEM_BUILD_BITCODE_LIBRARY=0 \
      -DNVSHMEM_BUILD_PYTHON_LIB=0; \
    cmake --build build --target nvshmem_transport_ibgda -j"$(nproc)"; \
    NEW=$(find build -name 'nvshmem_transport_ibgda.so.3*' -type f | head -1); \
    test -n "${NEW}"; \
    strings "${NEW}" | grep -q 'Zeroing ah_attr'; \
    cp "${WHEEL_LIB}/nvshmem_transport_ibgda.so.3" "${WHEEL_LIB}/nvshmem_transport_ibgda.so.3.stock"; \
    cp "${NEW}" "${WHEEL_LIB}/nvshmem_transport_ibgda.so.3"; \
    cd /; rm -rf /tmp/nvshmem /tmp/nvshmem.patch
