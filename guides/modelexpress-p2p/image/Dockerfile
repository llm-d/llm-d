FROM ghcr.io/llm-d/llm-d-cuda:v0.7.0

# The ModelExpress vLLM client plugin (registers the `mx` / `modelexpress`
# load-format). Pure Python, so no native build step. `--no-deps` keeps it
# from shadowing the image's pinned vllm / torch / nixl. The llm-d-cuda
# image ships `uv`, not `pip`, so install into a dedicated prefix and put it
# on PYTHONPATH (the image's venv stays untouched).
ARG MODELEXPRESS_VERSION=0.4.0
RUN uv pip install --target=/opt/modelexpress --no-deps --no-cache \
        "modelexpress==${MODELEXPRESS_VERSION}"
ENV PYTHONPATH=/opt/modelexpress
