# Artifacts

This page lists the llm-d release artifacts and dependencies:

1. [**CRDs**](#1-crds) — the Kubernetes Custom Resource Definitions used by llm-d
2. [**llm-d Router**](#2-llm-d-router) — the Helm chart and container images for the routing layer
3. [**Model Servers and Extensions**](#3-model-servers-and-extensions) — the inference engine images and extensions for advanced functionality
4. [**Well-Lit Path Guides**](#4-well-lit-path-guides) — deployment manifests and benchmark scripts for key user stories
5. [**Gateway Recipes**](#5-gateway-recipes) — optional recipes for installing Gateways and integrating them with llm-d

> [!IMPORTANT]
> llm-d follows a modular deployment pattern, enabling gradual feature 
> adoption. Users seeking a single CRD-driven deployment pattern should 
> consider KServe's [LLMInferenceService](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview).

## 1. GAIE CRDs

llm-d uses the APIs defined in the Gateway API Inference Extension (GAIE) project:

| CRD |  Purpose |
|-----|----------|
| [InferencePool](../api-reference/inferencepool.md) | Defines a pool of inference endpoints (model servers) and configures the EPP and proxy for LLM-aware routing. |
| [InferenceObjective](../api-reference/inferenceobjective.md) | Defines performance goals (priority, latency) for specific model workloads within a pool. |
| [InferenceModelRewrite](../api-reference/inferencemodelrewrite.md) | Specifies rules for rewriting model names in request bodies, enabling traffic splitting and canary rollouts. |

Manifests are published at [kubernetes-sigs/gateway-api-inference-extension/config/crd](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/crd) and can be installed like:

```bash
export GAIE_VERSION=v1.5.0
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

## 2. llm-d Router

llm-d Router is deployed via Helm. We offer a chart both Standalone and Gateway Mode:

| Chart | Version | OCI Registry | Description |
|-------|---------|--------------|-------------|
| **Standalone Mode** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone` | Deploys an InferencePool and EPP with a standalone Envoy proxy as sidecar in EPP pod  |
| **Gateway Mode** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool` | Deploys an InferencePool and EPP for use with an existing Kubernetes Gateway (e.g. Istio, AgentGateway, GKE) |

The charts are currently published by the Gateway API Inference Extension (GAIE) project (see [standalone mode source](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/standalone) and [gateway mode source](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/inferencepool)). Each well-lit path guides provides values files on top of the chart defaults to enable the functionality implemented in EPP.

> [!NOTE]
> In a future release, the Helm Charts will be published
> from the llm-d project rather than from GAIE.

### Images

llm-d releases the core EPP image as well as additional sidecar images for advanced functionality:

| Image | Description | Version |
|-------|-------------|---------|
| `ghcr.io/llm-d/llm-d-inference-scheduler` | Core EPP image | v0.8.0 |
| `ghcr.io/llm-d/llm-d-uds-tokenizer`       | Optional sidecar for EPP, enabling tokenization for precise cache aware routing | v0.8.0 |
| `ghcr.io/llm-d/llm-d-routing-sidecar`     | Optional sidecar for model servers, enabling KV cache transfer for P/D | v0.8.0 |
| `registry.k8s.io/gateway-api-inference-extension/latency-training-server` | Optional sidecar for EPP, for predicted-latency model training | v1.5.0 |
| `registry.k8s.io/gateway-api-inference-extension/latency-prediction-server` | Optional sidecar for EPP, for predicted-latency scheduling | v1.5.0 |

> [!NOTE]
> In a future release, the latency server images will be
> released from the llm-d/llm-d-latency-predictor repo.

## 3. Model Servers and Extensions

The llm-d stack supports vLLM and SGLang.

> [!IMPORTANT]
> llm-d validates each released guide against specific versions
> of each model server, but llm-d Router communicates with model
> servers over the OpenAI-compatible HTTP API and standard
> inference engine metrics, so any recent release should work.

### Upstream Images

We recommend using the upstream images for most guides:

| Engine        | Image             | Tag       |
|--------       |----------------   |--------   |
| **vLLM**      | `vllm/vllm-openai`| `v0.19.1` |
| **vLLM TPU**  | `vllm/vllm-tpu`   | `v0.18.0` |
| **SGLang**    | `lmsysorg/sglang` | `v0.5.10.post1` |

### Custom Images

In addition to the upstream images, llm-d also builds and releases vLLM images with features not yet merged into vLLM upstream such as:
* EFA support for AWS HPC networking
* GKE IB networking patches
* DeepEP patches for GB200 support
* RIXL support on AMD ROCm

| Image | Tag | Accelerator | Base OS | Architectures |
|-------|-----|-------------|---------|---------------|
| `ghcr.io/llm-d/llm-d-cuda`      | `v0.7.0` | NVIDIA GPU | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-cuda-gb200`| `v0.7.0` | NVIDIA GPU | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-aws`       | `v0.7.0` | NVIDIA GPU + EFA | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-rocm`      | `v0.7.0` | AMD ROCm | RHEL UBI9 | amd64 |
| `ghcr.io/llm-d/llm-d-xpu`       | `v0.7.0` | Intel XPU | Ubuntu 24.04 | amd64 |
| `ghcr.io/llm-d/llm-d-hpu`       | `v0.7.0` | Intel Gaudi HPU | Ubuntu 22.04 | amd64 |
| `ghcr.io/llm-d/llm-d-cpu`       | `v0.7.0` | CPU | RHEL UBI9 | amd64 |

### FS Offloading Extension

`llmd-fs-connector` adds filesystem offloading to vLLM's `OffloadingConnector`. It is released from [llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache/releases/tag/v0.8.0) as a python wheel and hosted on the following pypi registry https://llm-d.github.io/llm-d-kv-cache/simple/builds.

## 4. Well-Lit Path Guides

Well-Lit Paths are tested, benchmarked deployment recipes that show off llm-d's key user stories. Each guide lives under `guides/<path>/` and contains:

- **EPP Configurations** - Helm values files with EPP configurations for usage with the charts for llm-d Router.
- **Model Server Manifests** - Kustomize manifests for model server with labels and flags needed for usage with llm-d Router. 

> [!IMPORTANT]
> For some guides, we provide cloud provider specific
> settings. This is especially important for guides requiring
> IB and RoCE networking, which is not yet standardized.
> Users can adapt the examples to other platforms as needed.

See the [full list of guides](../well-lit-paths/README.md) for more details.


## 5. Gateways

llm-d Router supports optional integration with Kubernetes Gateways. These are the versions we test against for the `v0.7.0` release:

| Dependency | Tested Versions | Notes |
|------------|-----------------|-------|
| Gateway API CRDs | `v1.5.x` | Kubernetes SIG (required if using a Gateway) |
| Istio | `1.29.x` | Default gateway provider |
| AgentGateway | `v1.0.x` | Preferred for new deployments |
| kgateway | `v2.2.x` | **Deprecated** — will be removed in the next release |

Install instructions live under [`guides/recipes/gateway/`](https://github.com/llm-d/llm-d/tree/main/guides/recipes/gateway).

## Source Repositories

### Core Libraries

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d](https://github.com/llm-d/llm-d) | — | Main repo: docs, Dockerfiles, guides, CI |
| [llm-d/llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) | Go | EPP routing engine and P/D sidecar |
| [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor) | Python | XGBoost training and prediction server |
| [llm-d/llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) | Go, Python, CPP | KV-cache block locality indexer, FS offloading |
| [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | Go | SLO-aware workload autoscaler |
| [llm-d-incubation/llm-d-async](https://github.com/llm-d-incubation/llm-d-async) | Go | Asynchronous request processor for latency insensitive traffic |
| [llm-d-incubation/batch-gateway](https://github.com/llm-d-incubation/batch-gateway) | Go | OpenAI-compatible API for submitting, tracking, and managing batch inference jobs.

### Supporting Libraries

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) | Python | Benchmarking framework |
| [llm-d/llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) | Go | GPU-free vLLM simulator |

