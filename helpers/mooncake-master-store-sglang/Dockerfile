# syntax=docker/dockerfile:1
FROM python:3.12-slim-trixie
ARG MOONCAKE_VERSION=0.3.13
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libibverbs1 ibverbs-providers libnuma1 libcurl4t64 libstdc++6 tini \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
        mooncake-transfer-engine-non-cuda==${MOONCAKE_VERSION}

ENTRYPOINT ["tini", "-g", "--"]
CMD ["bash"]
