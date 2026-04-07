# P/D Disaggregation SGLang

## **Automated Testing Coverage** : None (currently, not part of nightly testing by llm-d maintainers)

## Overview

This document provides complete steps for deploying PD disaggregation service on Kubernetes cluster using SGLang inference server with NIXL as the data transfer backend. PD disaggregation separates the prefill and decode phases of inference, allowing for more efficient resource utilization and improved throughput.

In this example, we demonstrate a deployment of `Qwen3-14B` model with:

- 1 Decode worker with TP=4
- 4 Prefill workers with TP=1

## Hardware Requirements

8 Nvidia GPUs of any kind.

## Installation

For the installation and usage follow our [PD disaggregation well-lit path guide](./README.md#prerequisites) with a small change in the [Deploy](./README.md#deploy) section:
```bash
cd guides/pd-disaggregation
export INFERENCE_SERVER=sglang
helmfile apply -n ${NAMESPACE}
```

## Technical Notes

- KV Cache Transfer: SGLang’s NIXL (UCX) integration defaults to TCP fallback if RDMA is unavailable. For optimal performance and minimized latency during transfer, it is recommended to utilize RDMA-capable transports (e.g., InfiniBand or RoCE).
- Metrics Compatibility: Please note that vllm and SGLang utilize different metric naming conventions. Metrics are mapped within the GAIE configuration.
