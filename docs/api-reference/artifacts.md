# Artifacts

This page lists the llm-d release artifacts and dependencies:

1. [**CRDs**](#1-crds) — the Kubernetes Custom Resource Definitions used by llm-d
2. [**llm-d Router**](#2-llm-d-router) — the Helm chart and container images for the routing layer
3. [**Model Servers and Extensions**](#3-model-servers-and-extensions) — the inference engine images and extensions for advanced functionality
4. [**Well-Lit Path Guides**](#4-well-lit-path-guides) — deployment manifests and benchmark scripts for key user stories
5. [**Gateway Recipes**](#5-gateway-recipes) — optional recipes for installing Gateways and integrating them with llm-d
6. [**Async Processor**](#6-async-processor) — the Helm chart and container image for asynchronous, queue-based request processing

> [!IMPORTANT]
> llm-d follows a modular deployment pattern, enabling gradual feature
> adoption. Users seeking a single CRD-driven deployment pattern should
> consider KServe's [LLMInferenceService](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview).

## 1. GAIE CRDs

llm-d uses the APIs defined in the Gateway API Inference Extension (GAIE) project:

| CRD |  Purpose | Provider |
|-----|----------|----------|
| [InferencePool](../api-reference/inferencepool.md) | Defines a pool of inference endpoints (model servers) and configures the EPP and proxy for LLM-aware routing. | `kubernetes-sigs/gateway-api-inference-extension` |
| [InferenceObjective](../api-reference/inferenceobjective.md) | Defines performance goals (priority, latency) for specific model workloads within a pool. | `llm-d/llm-d-router` |
| [InferenceModelRewrite](../api-reference/inferencemodelrewrite.md) | Specifies rules for rewriting model names in request bodies, enabling traffic splitting and canary rollouts. | `llm-d/llm-d-router` |

The versions for the GAIE CRDs are derived from the [env.sh file](../../guides/env.sh). This makes upgrades easy as they are stored in a common location across guides.

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
```

Manifests are published at [kubernetes-sigs/gateway-api-inference-extension/config/crd](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/crd) and can be installed like:

```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```

## 2. llm-d Router

llm-d Router is deployed via Helm. We offer a chart both Standalone and Gateway Mode:

| Chart | Version | OCI Registry | Description |
|-------|---------|--------------|-------------|
| **Standalone Mode** | v0 | `oci://ghcr.io/llm-d/charts/llm-d-router-standalone` | Deploys an InferencePool and EPP with a standalone sidecar proxy (Envoy by default, agentgateway also supported via `router.proxy.proxyType=agentgateway`) in the EPP pod  |
| **Gateway Mode** | v0 | `oci://ghcr.io/llm-d/charts/llm-d-router-gateway` | Deploys an InferencePool and EPP for use with an existing Kubernetes Gateway (e.g. Istio, AgentGateway, Envoy AI Gateway, GKE) |

The charts are currently published by the Gateway API Inference Extension (GAIE) project (see [standalone mode source](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/standalone) and [gateway mode source](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/inferencepool)). Each well-lit path guides provides values files on top of the chart defaults to enable the functionality implemented in EPP.

> [!NOTE]
> In a future release, the Helm Charts will be published
> from the llm-d project rather than from GAIE.

### Images

llm-d releases the core EPP image as well as additional sidecar images for advanced functionality:

| Image | Description | Version |
|-------|-------------|---------|
| `ghcr.io/llm-d/llm-d-router-endpoint-picker` | Core EPP image | main |
| `ghcr.io/llm-d/llm-d-router-disagg-sidecar`     | Optional sidecar for model servers, enabling KV cache transfer for P/D | v0.10.0 |
| `registry.k8s.io/gateway-api-inference-extension/latency-training-server` | Optional sidecar for EPP, for predicted-latency model training | v1.5.0 |
| `registry.k8s.io/gateway-api-inference-extension/latency-prediction-server` | Optional sidecar for EPP, for predicted-latency scheduling | v1.5.0 |

> [!IMPORTANT]
> The EPP image was renamed from `ghcr.io/llm-d/llm-d-inference-scheduler`
> in llm-d v0.8.0. The old package has been removed, so deployments must
> update the image repository as well as the tag.

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
|---------------|-------------------|-----------|
| **vLLM**             | `docker.io/vllm/vllm-openai`            | `v0.26.0`     |
| **vLLM Omni**        | `docker.io/vllm/vllm-omni`              | `v0.26.0`     |
| **vLLM TPU**         | `docker.io/vllm/vllm-tpu`               | `v0.26.0`     |
| **vLLM XPU**         | `docker.io/vllm/vllm-openai-xpu`        | `v0.26.0`     |
| **vLLM ROCM**        | `docker.io/vllm/vllm-openai-rocm`       | `v0.26.0`     |
| **vLLM ROCM Omni**   | `docker.io/vllm/vllm-omni-rocm`         | `v0.24.1`     |
| **vLLM CPU**         | `docker.io/vllm/vllm-openai-cpu`        | `v0.26.0`     |
| **SGLang**           | `docker.io/lmsysorg/sglang`             | `v0.5.16`   |
| **TRTLLM**           | `nvcr.io/nvidia/tensorrt-llm/release`   | `1.3.0rc23`   |

### Custom Images

In addition to the upstream images, llm-d also builds and releases vLLM images with features not yet merged into vLLM upstream such as:

* GKE IB networking patches
* DeepEP patches for GB200 support
* RIXL support on AMD ROCm

| Image | Tag | Accelerator | Base OS | Architectures |
|-------|-----|-------------|---------|---------------|
| `ghcr.io/llm-d/llm-d-cuda` (deprecated) | `v0.9.0` | NVIDIA GPU | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-aws` (deprecated)  | `v0.9.0` | NVIDIA GPU + EFA | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-rocm`              | `v0.9.0` | AMD ROCm | RHEL UBI9 | amd64 |
| `ghcr.io/llm-d/llm-d-xpu`               | `v0.9.0` | Intel XPU | Ubuntu 24.04 | amd64 |
| `ghcr.io/llm-d/llm-d-xpu-sglang`        | `v0.9.0` | Intel XPU | Ubuntu 24.04 | amd64 |
| `ghcr.io/llm-d/llm-d-cpu`               | `v0.9.0` | CPU | RHEL UBI9 | amd64 |

## 4. Well-Lit Path Guides

Well-Lit Paths are tested, benchmarked deployment recipes that show off llm-d's key user stories. Each guide lives under `guides/<path>/` and contains:

* **EPP Configurations** - Helm values files with EPP configurations for usage with the charts for llm-d Router.
* **Model Server Manifests** - Kustomize manifests for model server with labels and flags needed for usage with llm-d Router.

> [!IMPORTANT]
> For some guides, we provide cloud provider specific
> settings. This is especially important for guides requiring
> IB and RoCE networking, which is not yet standardized.
> Users can adapt the examples to other platforms as needed.

See the [full list of guides](../well-lit-paths/README.md) for more details.

## 5. Gateways

llm-d Router supports optional integration with Kubernetes Gateways. These are the versions we test against for the `v0.10.0` release:

| Dependency | Tested Versions | Notes |
|------------|-----------------|-------|
| Gateway API CRDs | `v1.5.x` | Kubernetes SIG (required if using a Gateway) |
| Istio | `1.29.x` | Default gateway provider |
| AgentGateway | `v1.4.x` | Preferred for new deployments |

Install instructions live under [`guides/recipes/gateway/`](https://github.com/llm-d/llm-d/tree/main/guides/recipes/gateway).

## 6. Async Processor

The [Async Processor](https://github.com/llm-d/llm-d-async) is an optional component that pulls inference requests from a message queue, gates dispatch on pool capacity, and forwards them to llm-d Router. It is deployed via Helm — see the [asynchronous processing guide](../../guides/batch-serving/asynchronous-processing/README.md) and the [operations guide](../operations/async-processor.md).

| Chart | Version | OCI Registry | Description |
|-------|---------|--------------|-------------|
| **Async Processor** | v0.9.0 | `oci://ghcr.io/llm-d/charts/llm-d-async` | Deploys the async processor with its queue backend (GCP Pub/Sub or Redis), worker pools, and dispatch gates |

### Images

| Image | Description | Version |
|-------|-------------|---------|
| `ghcr.io/llm-d/llm-d-async` | Asynchronous dispatch processor for latency-insensitive traffic | v0.9.0 |

Clients that publish requests or consume results can import the Go modules released alongside the image — `github.com/llm-d/llm-d-async/api`, `/pipeline`, and `/producer`, each tagged `v0.9.0`.

> [!NOTE]
> The chart was renamed from `async-processor` to `llm-d-async` in v0.8.0, and chart versions now
> carry a leading `v`. A Deployment's selector is immutable, so moving from an existing
> `async-processor` install requires uninstall and reinstall rather than `helm upgrade`.

## Source Repositories

### Core Libraries

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d](https://github.com/llm-d/llm-d) | — | Main repo: docs, Dockerfiles, guides, CI |
| [llm-d/llm-d-router](https://github.com/llm-d/llm-d-router) | Go | EPP routing engine and P/D sidecar |
| [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor) | Python | XGBoost training and prediction server |
| [llm-d/llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) | Go, Python, CPP | KV-cache block locality indexer, FS offloading |
| [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | Go | SLO-aware workload autoscaler |
| [llm-d/llm-d-async](https://github.com/llm-d/llm-d-async) | Go | Asynchronous request processor for latency insensitive traffic |
| [llm-d/llm-d-batch-gateway](https://github.com/llm-d/llm-d-batch-gateway) | Go | OpenAI-compatible API for submitting, tracking, and managing batch inference jobs.

### Supporting Libraries

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) | Python | Benchmarking framework |
| [llm-d/llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) | Go | GPU-free vLLM simulator |
