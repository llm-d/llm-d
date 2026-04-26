# Installing a Guide

## Introduction

An llm-d [well-lit path](../docs/wip-docs-new/getting-started/README.md#well-lit-paths) is a tested, benchmarked deployment recipe — not a one-liner install command, but a series of technical choices tuned for a specific production pattern. Each guide provides sensible defaults, but users should review and [customize](04_customizing_a_guide.md) the configuration for their own models, hardware, and traffic patterns.

## Deployment Choices

### Proxy

llm-d uses the [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) to extend standard Kubernetes proxies into inference-aware proxies. The proxy receives client traffic and consults the llm-d scheduler (EPP) via [ext-proc](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) to route each request to the optimal model server. For a deeper look at how this works, see the [proxy architecture doc](../docs/wip-docs-new/architecture/core/proxy.md).

There are two deployment modes to choose from:

#### Option 1: Standalone (default)

The standalone chart deploys the scheduler with an Envoy sidecar in a single pod. No external proxy infrastructure is required — clients send requests directly to the scheduler service. This is the simplest path and is recommended for getting started, batch inference, RL post-training workloads, and environments where [Gateway API](https://gateway-api.sigs.k8s.io/) adds unnecessary operational overhead.

```bash
helm install <guide>-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/<guide>/scheduler/<guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} --version v1.4.0
```

#### Option 2: With Gateway API proxy

For production online serving, users may want to deploy a full [Gateway API](https://gateway-api.sigs.k8s.io/) proxy (Istio, Agentgateway, GKE Gateway, etc.). Gateway API is pluggable by design — beyond basic traffic routing, gateway providers offer a rich ecosystem of capabilities including TLS termination, rate limiting, authorization and quota management, multi-service routing, and observability integration. These are production concerns that would otherwise need to be solved separately. We have seen significant performance and architectural benefits from using Gateway API in production, but it comes at the cost of deploying and managing a gateway control plane and provider. Users who do not need these capabilities should prefer the standalone path.

```bash
helm install <guide>-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/<guide>/scheduler/<guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} --version v1.4.0
```

##### Deploying the Gateway

If you chose the Gateway API proxy path, you also need a Gateway resource and proxy control plane. Set these up before installing the inferencepool chart above:

1. [Install your proxy control plane](prereq/gateway-provider/README.md) (Istio, Agentgateway, GKE, etc.)
2. [Create a Gateway resource](recipes/gateway/README.md) for your provider

See the [gateway recipes](recipes/gateway/README.md) for provider-specific configuration options.

### Model Serving

The model server configuration is where most of the meaningful technical choices live. Each guide provides a default configuration, but users should consider the following when adapting a guide to their workload:

#### Parallelism strategy

How a model is distributed across devices has a direct impact on throughput, latency, and memory utilization:

- **Tensor Parallelism (TP)** — splits individual layers across devices, reducing per-device memory at the cost of inter-device communication. Common for large dense models that don't fit on a single device.
- **Data Parallelism (DP)** — runs independent model replicas across devices. Scales throughput linearly but requires each replica to fit in device memory.
- **Expert Parallelism (EP)** — distributes experts in Mixture-of-Experts (MoE) architectures across devices, enabling models like DeepSeek-R1 to be served across multiple nodes.
- **Pipeline Parallelism (PP)** — splits model layers sequentially across devices. Useful for very deep models but introduces pipeline bubbles.

#### Model architecture

The type and size of model influences serving configuration significantly:

- **Dense vs. MoE** — MoE models activate only a subset of parameters per token, offering better throughput per FLOP but requiring more total memory and potentially EP.
- **Context length** — longer context windows require more KV-cache memory, affecting how many concurrent requests a replica can handle.
- **Quantization** — reduced precision (FP8, INT8, INT4) decreases memory usage and can improve throughput, but may affect output quality depending on the model and technique.

#### Advanced serving techniques

- **Speculative decoding** — uses a smaller draft model to predict tokens that the main model then verifies in parallel, reducing latency for generation-heavy workloads.
- **LoRA adapters** — serves multiple fine-tuned model variants from a single base model, reducing memory overhead for multi-tenant deployments.
- **Prefix caching** — reuses KV-cache entries across requests sharing common prefixes (system prompts, few-shot examples), reducing time-to-first-token.
- **Chunked prefill** — breaks long prompt processing into chunks to interleave with decode steps, reducing latency spikes for other in-flight requests.

And more.

## Prerequisites

- Have the [proper client tools installed](../helpers/client-setup/README.md)
- **If** serving a model that requires a hugging face token, [Create the `llm-d-hf-token` secret](../helpers/client-setup/README.md#huggingface-token) in your target namespace.
  - **NOTE**: MOST examples use `Qwen3-32B` or `Deepseek-r1-0528`. Both of these models do not require a token.
- [Choose an llm-d version](../helpers/client-setup/README.md#llm-d-version)

## Installation

### 1. Create a namespace

```bash
export NAMESPACE=<your-namespace>
kubectl create namespace ${NAMESPACE}
```

### 2. Deploy the scheduler

Choose standalone or proxy mode above and run the corresponding `helm install` command. See [scheduler recipes](recipes/scheduler/README.md) for details on values layering and feature toggles.

### 3. Deploy the model server

```bash
kustomize build guides/<guide>/modelserver/<accelerator>/<server>/ | kubectl apply -n ${NAMESPACE} -f -
```

## Cleanup

```bash
helm uninstall <guide>-scheduler -n ${NAMESPACE}
kustomize build guides/<guide>/modelserver/<accelerator>/<server>/ | kubectl delete -n ${NAMESPACE} -f -
```
