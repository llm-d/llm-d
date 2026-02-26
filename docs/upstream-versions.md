# Upstream Dependency Version Tracking

> This file is the source of truth for the upstream dependency monitor workflow.
> It tracks all external dependencies pinned in this repository, their current versions,
> and where they are pinned. The `upstream-monitor` agentic workflow reads this file daily
> to detect when upstream projects release new versions that may break llm-d.

## Docker Build Dependencies

Pinned in `docker/Dockerfile.cuda` (and variants):

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **vLLM** | `d7de043d55d1dd629554467e23874097e1c48993` | commit SHA | `docker/Dockerfile.cuda` line 68 (`VLLM_COMMIT_SHA`) | [vllm-project/vllm](https://github.com/vllm-project/vllm) |
| **CUDA** | `12.9.1` | version | `docker/Dockerfile.cuda` lines 17-19 | [NVIDIA CUDA](https://developer.nvidia.com/cuda-toolkit) |
| **Python** | `3.12` | version | `docker/Dockerfile.cuda` line 23 | [python/cpython](https://github.com/python/cpython) |
| **GDRCOPY** | `v2.5.1` | tag | `docker/Dockerfile.cuda` line 35 | [NVIDIA/gdrcopy](https://github.com/NVIDIA/gdrcopy) |
| **UCX** | `v1.20.0` | tag | `docker/Dockerfile.cuda` line 38 | [openucx/ucx](https://github.com/openucx/ucx) |
| **NVSHMEM** | `v3.4.5-0` | tag | `docker/Dockerfile.cuda` line 42 | [NVIDIA/nvshmem](https://github.com/NVIDIA/nvshmem) |
| **NIXL** | `0.9.0` | version | `docker/Dockerfile.cuda` line 46 | [ai-dynamo/nixl](https://github.com/ai-dynamo/nixl) |
| **InfiniStore** | `0.2.33` | version | `docker/Dockerfile.cuda` line 49 | [bytedance/InfiniStore](https://github.com/bytedance/InfiniStore) |
| **LMCache** | `v0.3.13` | tag | `docker/Dockerfile.cuda` line 51 | [LMCache/LMCache](https://github.com/LMCache/LMCache) |
| **DeepEP** | `c66b1463601af5d127027fb459517fb97adab30d` | commit SHA (fork) | `docker/Dockerfile.cuda` line 57 | [smarterclayton/DeepEP](https://github.com/smarterclayton/DeepEP) (fork of [deepseek-ai/DeepEP](https://github.com/deepseek-ai/DeepEP)) |
| **DeepGEMM** | `v2.1.0` | tag | `docker/Dockerfile.cuda` line 60 | [deepseek-ai/DeepGEMM](https://github.com/deepseek-ai/DeepGEMM) |
| **FlashInfer** | `v0.5.3` | tag | `docker/Dockerfile.cuda` line 64 | [flashinfer-ai/flashinfer](https://github.com/flashinfer-ai/flashinfer) |
| **PyTorch** | `2.9.1` | version | `docker/constraints.txt` line 1 | [pytorch/pytorch](https://github.com/pytorch/pytorch) |

## Helm Chart Dependencies

Pinned in guide helmfiles (`guides/*/helmfile.yaml.gotmpl`):

| Dependency | Current Pin | File Location | Upstream Repo / Registry |
|-----------|-------------|---------------|--------------------------|
| **llm-d-infra chart** | `v1.3.6` | All `helmfile.yaml.gotmpl` files | [llm-d-incubation/llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra) (`https://llm-d-incubation.github.io/llm-d-infra/`) |
| **InferencePool chart** | `v1.3.0` | All `helmfile.yaml.gotmpl` files | [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) (`oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool`) |
| **llm-d-modelservice chart** | `v0.4.5` | All `helmfile.yaml.gotmpl` files | [llm-d-incubation/llm-d-modelservice](https://github.com/llm-d-incubation/llm-d-modelservice) (`https://llm-d-incubation.github.io/llm-d-modelservice/`) |

## Gateway Provider Dependencies

Pinned in `guides/prereq/gateway-provider/`:

| Dependency | Current Pin | File Location | Upstream Repo |
|-----------|-------------|---------------|---------------|
| **Gateway API CRDs** | `v1.4.0` | `install-gateway-provider-dependencies.sh` line 39 | [kubernetes-sigs/gateway-api](https://github.com/kubernetes-sigs/gateway-api) |
| **Gateway API Inference Extension CRDs** | `v1.3.0` | `install-gateway-provider-dependencies.sh` line 46 | [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) |
| **Istio** | `1.28.1` | `istio.helmfile.yaml` | [istio/istio](https://github.com/istio/istio) |
| **kgateway** | `v2.1.1` | `kgateway.helmfile.yaml` | [kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway) |
| **agentgateway** | `v2.2.0-beta.3` | `agentgateway.helmfile.yaml` | [kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway) |

## CI Workflow Dependencies

| Dependency | Current Pin | File Location | Notes |
|-----------|-------------|---------------|-------|
| **LeaderWorkerSet (LWS)** | `0.7.0` | `e2e-wide-ep-accelerator-test.yaml` line 387 | Also in nightly LWS workflows |
| **InferencePool (GKE)** | `v1.2.1` | `e2e-wide-ep-accelerator-gke.yaml` line 45 | **STALE** — helmfiles use v1.3.0 |

## Hardware-Specific vLLM Images

| Variant | Current Pin | File Location | Upstream |
|---------|-------------|---------------|----------|
| **vLLM Gaudi (HPU)** | `1.22.0` | `guides/*/values_hpu.yaml` | [HabanaAI/vllm-fork](https://github.com/HabanaAI/vllm-fork) |
| **vLLM TPU** | `v0.13.2-ironwood` | `guides/*/values_tpu.yaml` | [vllm-project/vllm](https://github.com/vllm-project/vllm) |
