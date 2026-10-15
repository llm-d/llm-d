ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# `--no-deps` keeps the client from shadowing the image's pinned
# vllm / torch / nixl, so its remaining deps must be listed explicitly:
# google-crc32c (artifact chunk checksums) is not in the vLLM base image.
# The 0.5.x wheels ship a prebuilt VMM allocator extension; no build step.
ARG MODELEXPRESS_VERSION=0.5.0
ARG GOOGLE_CRC32C_VERSION=1.8.0
RUN python3 -m pip install --target=/opt/modelexpress --no-deps --no-cache-dir \
        "modelexpress==${MODELEXPRESS_VERSION}" \
        "google-crc32c==${GOOGLE_CRC32C_VERSION}"
ENV PYTHONPATH=/opt/modelexpress
