ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# The ModelExpress vLLM client plugin (registers the `mx` / `modelexpress`
# load-format). Pure Python, so no native build step. `--no-deps` keeps it
# from shadowing the image's pinned vllm / torch / nixl. Install into a
# dedicated prefix and put it on PYTHONPATH so the base image stays untouched.
ARG MODELEXPRESS_VERSION=0.4.0
RUN python3 -m pip install --target=/opt/modelexpress --no-deps --no-cache-dir \
        "modelexpress==${MODELEXPRESS_VERSION}"
ENV PYTHONPATH=/opt/modelexpress
