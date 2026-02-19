FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y git \
    # requests
    openssl \
    # vegeta \
    # grpcurl \
    # websocat \
    wget \
    curl \
    # editors
    vim \
    nano \
    # parsing / preocessors
    jq \
    # # processes + files + file management
    zip \
    unzip \
    xz-utils \
    procps \
    psmisc \
    lsof \
    strace \
    ltrace \
    gdb \
    htop \
    btop \
    fio \
    # cluster management + logs
    kubectl \
    less \
    # specialty device management
    pciutils \
    rdma-core \
    ibverbs-providers \
    infiniband-diags \
    nvme-cli \
    # misc
    glow \
    tree \
    rsync \
    promtool

# install stern
RUN cd /tmp && \
    curl -sLO https://github.com/stern/stern/releases/download/v1.33.0/stern_1.33.0_linux_amd64.tar.gz && \
    tar -xvf stern_1.33.0_linux_amd64.tar.gz && \
    rm LICENSE stern_1.33.0_linux_amd64.tar.gz && \
    mv stern /usr/local/bin

# install yq
RUN wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/local/bin/yq &&\
    chmod +x /usr/local/bin/yq

RUN echo 'source <(kubectl completion bash)' >>~/.bashrc && \
    echo 'alias k=kubectl' >>~/.bashrc && \
    echo 'export TERM=xterm-256color COLORTERM=truecolor' >>~/.bashrc

WORKDIR /app

COPY requirements.txt requirements.txt

# hadolint ignore=DL3013
RUN pip install torch --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install -r requirements.txt

ARG VLLM_SHA="d7de043d55d1dd629554467e23874097e1c48993"
RUN git clone https://github.com/vllm-project/vllm.git && \
    cd vllm && git checkout ${VLLM_SHA} && \
    git config --system --add safe.directory /app/vllm

COPY install-max-token-guidellm-hack.sh /app/install-max-token-guidellm-hack.sh
RUN /app/install-max-token-guidellm-hack.sh && \
    rm /app/install-max-token-guidellm-hack.sh

COPY prepare-inference.sh /app/prepare-inference.sh

COPY run-guide-llm.sh /app/run-guide-llm.sh

WORKDIR /app/vllm/benchmarks
RUN wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json

COPY README.md /app/README.md

WORKDIR /app
ENTRYPOINT ["/bin/bash"]
