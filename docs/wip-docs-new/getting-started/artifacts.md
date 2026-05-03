# Artifacts

This page is the inventory of artifacts shipped with the llm-d **v0.7.0** release. It is organized around the five things you actually need to deploy llm-d:

1. [**CRDs**](#1-crds) — the Kubernetes Custom Resource Definitions used by the router
2. [**llm-d Router**](#2-llm-d-router) — the Helm chart and container images for the routing layer
3. [**Model Server Images**](#3-model-server-images) — the inference engine images
4. [**Well-Lit Path Guides**](#4-well-lit-path-guides) — tested deployment patterns and benchmark scripts
5. [**Gateways**](#5-gateways) — optional Gateway-API providers we test against

---

## 1. CRDs

llm-d builds on top of the [Gateway API Inference Extension (GIE)](https://github.com/kubernetes-sigs/gateway-api-inference-extension) project, which defines the CRDs the router consumes.

| CRD | API Group | Version | Purpose |
|-----|-----------|---------|---------|
| [InferencePool](../api-reference/inferencepool.md) | `inference.networking.k8s.io` | `v1` | Defines a pool of inference endpoints (model servers) and configures the EPP and Gateways for inference-optimized routing. |
| [InferenceObjective](../api-reference/inferenceobjective.md) | `inference.networking.x-k8s.io` | `v1alpha2` | Defines performance goals (priority, latency) for specific model workloads within a pool. |
| [InferenceModelRewrite](../api-reference/inferencemodelrewrite.md) | `inference.networking.x-k8s.io` | `v1alpha2` | Specifies rules for rewriting model names in request bodies, enabling traffic splitting and canary rollouts. |

### Where the assets live

The CRD manifests are published by the upstream GIE project at [kubernetes-sigs/gateway-api-inference-extension/config/crd](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/crd). The v0.7.0 release of llm-d pins **GIE v1.5.0**.

### How to install

Apply the CRDs straight from the upstream repository, pinned to the GIE release tag:

```bash
export GAIE_VERSION=v1.5.0
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

If you are running in **Gateway mode** (rather than the default Standalone Mode — see [section 5](#5-gateways)), you also need the upstream Gateway API CRDs:

```bash
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=v1.5.0"
```

---

## 2. llm-d Router

The llm-d Router is the intelligent load-balancing layer. It is deployed via an upstream Helm chart and a set of container images published by the llm-d project.

### Helm Chart

| Chart | Version | OCI Registry | When to use |
|-------|---------|--------------|-------------|
| **Standalone Router** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone` | Default — bundles an Envoy proxy alongside the EPP. No Gateway provider required. |
| **InferencePool + EPP** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool` | Use when you have an existing Kubernetes Gateway (Istio, AgentGateway, GKE, kgateway). |

Both charts are published by the upstream [Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) project. The well-lit path guides apply guide-specific values files on top of the chart defaults.

### Container Images

These images are built and published by llm-d to `ghcr.io/llm-d/`. They are referenced by the values files used in the guides — you do not normally pull them by hand.

| Image | Description | Version |
|-------|-------------|---------|
| `ghcr.io/llm-d/llm-d-inference-scheduler` | EPP — the inference-aware request router | v0.8.0 |
| `ghcr.io/llm-d/llm-d-routing-sidecar` | P/D disaggregation sidecar for KV transfer coordination | v0.8.0 |
| `ghcr.io/llm-d/llm-d-uds-tokenizer` | Unix-domain-socket tokenizer sidecar | v0.8.0 |
| `ghcr.io/llm-d/llm-d-kv-cache` | KV-cache block locality indexer library | v0.7.1 |
| `ghcr.io/llm-d/llm-d-workload-variant-autoscaler` | SLO-aware workload autoscaler (optional) | v0.7.0 |
| `ghcr.io/llm-d/llm-d-inference-sim` | GPU-free vLLM simulator (testing) | v0.8.2 |
| `ghcr.io/llm-d/llm-d-rdma-tools` | RDMA diagnostic and testing utilities | v0.7.0 |

### Image Tags

| Tag Pattern | Meaning |
|-------------|---------|
| `v0.7.0` | Release tag — pinned, immutable |
| `latest` | Latest build from `main` — rolling |
| `sha-<short>` | Specific commit build |
| `pr-<number>` | Build from a pull request (dev only) |

> Development images use the `-dev` suffix in the image name (e.g., `llm-d-inference-scheduler-dev`). Release images drop the suffix.

---

## 3. Model Server Images

llm-d does **not** require a custom model server image. The router communicates with model servers over the OpenAI-compatible HTTP API and standard inference engine metrics, so any recent vLLM or SGLang release should work.

### Use Upstream Images

For most users we recommend pulling the upstream image directly:

| Engine | Upstream Image | Notes |
|--------|----------------|-------|
| **vLLM** | `vllm/vllm-openai` | Primary engine. Any recent release should work. |
| **SGLang** | `lmsysorg/sglang` | Supported on CUDA. |

We test each llm-d release against the specific upstream versions listed below, but **any release should be fine** for production use unless you depend on a feature that landed in a newer version.

### Tested Upstream Versions (v0.7.0)

These are the versions we pin and test against for the v0.7.0 release.

| Dependency | Version | Purpose |
|------------|---------|---------|
| **vLLM** | v0.17.1 | Primary inference engine |
| **CUDA** | 12.9.1 | GPU compute runtime |
| **Python** | 3.12 | Runtime |
| **PyTorch** | 2.9.1 | ML framework |
| **NIXL** | 0.10.0 | KV-cache transport for P/D disaggregation |
| **LMCache** | v0.3.14 | Tiered KV-cache offloading |
| **InfiniStore** | 0.2.33 | Distributed cache storage backend |
| **DeepEP** | llm-d-release-v0.5.1 | Expert-parallelism communication |
| **DeepGEMM** | v2.1.1.post3 | High-performance inference compute |
| **FlashInfer** | v0.6.1 | Efficient attention kernels |
| **NVSHMEM** | v3.5.19-1 | GPU-side RDMA communication |
| **UCX** | v1.20.0 | Unified communication framework |
| **GDRCopy** | v2.5.2 | GPU direct RDMA memory copies |
| **LeaderWorkerSet** | v0.7.0 | Multi-node pod orchestration (Wide EP) |

See [`docs/upstream-versions.md`](https://github.com/llm-d/llm-d/blob/main/docs/upstream-versions.md) for the authoritative source.

### llm-d-Built vLLM Images

We also publish vLLM images built by the llm-d project. These bundle vLLM with the libraries needed by certain well-lit paths (NIXL, DeepEP, LMCache, etc.) and target accelerators or environments that the default upstream images do not cover.

| Image | Accelerator | Base OS | Architectures |
|-------|-------------|---------|---------------|
| `ghcr.io/llm-d/llm-d-cuda:v0.7.0` | NVIDIA CUDA | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-cuda:v0.7.0-debug` | NVIDIA CUDA (debug build) | RHEL UBI9 | amd64 |
| `ghcr.io/llm-d/llm-d-aws` | NVIDIA CUDA + EFA | RHEL UBI9 | amd64, arm64 |
| `ghcr.io/llm-d/llm-d-rocm` | AMD ROCm | RHEL UBI9 | amd64 |
| `ghcr.io/llm-d/llm-d-xpu` | Intel XPU | Ubuntu 24.04 | amd64 |
| `ghcr.io/llm-d/llm-d-hpu` | Intel Gaudi HPU | Ubuntu 22.04 | amd64 |
| `ghcr.io/llm-d/llm-d-cpu` | CPU | RHEL UBI9 | amd64 |

Hardware-specific vLLM forks bundled inside the images above:

| Variant | Version | Upstream |
|---------|---------|----------|
| vLLM Gaudi (HPU) | 1.22.0 | [HabanaAI/vllm-fork](https://github.com/HabanaAI/vllm-fork) |
| vLLM TPU | v0.13.2-ironwood | [vllm-project/vllm](https://github.com/vllm-project/vllm) |

> The project is migrating toward consuming upstream vLLM images directly, with a thin llm-d add-on layer for llm-d-specific components. See [#1112](https://github.com/llm-d/llm-d/issues/1112).

---

## 4. Well-Lit Path Guides

Well-Lit Paths are tested, benchmarked deployment recipes that show off llm-d's key features. Each guide lives under `guides/<path>/` and contains:

- **Deployable manifests** — Helm values and Kustomize overlays
- **Benchmark scripts** — reproducible runs against a baseline configuration
- **Tunable knobs** — documented configuration for performance tuning

### Reusable Recipe Building Blocks

Shared Kustomize bases live in `guides/recipes/` and are composed by each guide:

| Recipe | Path | Description |
|--------|------|-------------|
| **Gateway** | `guides/recipes/gateway/` | Base gateway manifest with provider overlays (Istio, AgentGateway, GKE, kgateway) |
| **InferencePool** | `guides/recipes/scheduler/` | InferencePool + EPP deployment |
| **vLLM** | `guides/recipes/modelserver/` | Model server base with standard overlay |

### Available Guides

| Guide | Path | What it demonstrates |
|-------|------|----------------------|
| [Optimized Baseline](../well-lit-paths/optimized-baseline.md) | `guides/optimized-baseline/` | LLM-aware load balancing vs. round-robin baseline |
| [Predicted Latency Routing](../well-lit-paths/predicted-latency.md) | `guides/predicted-latency-based-scheduling/` | XGBoost-based latency-aware scheduling and SLO admission |
| [Precise Prefix Cache Routing](../well-lit-paths/precise-prefix-cache-aware.md) | `guides/precise-prefix-cache-aware/` | Real-time prefix-cache-aware routing |
| [Tiered Prefix Cache](../well-lit-paths/tiered-prefix-cache.md) | `guides/tiered-prefix-cache/` | KV offloading to CPU RAM, NVMe, or network storage |
| [P/D Disaggregation](../well-lit-paths/pd-disaggregation.md) | `guides/pd-disaggregation/` | Separated prefill and decode workers |
| [Wide Expert-Parallelism](../well-lit-paths/wide-expert-parallelism.md) | `guides/wide-ep-lws/` | Multi-node DP/EP for large MoE models |
| [Flow Control](../well-lit-paths/flow-control.md) | `guides/flow-control/` | Multi-tenant fairness and priority queuing |
| [Workload Autoscaling](../well-lit-paths/workload-autoscaling.md) | `guides/workload-autoscaling/` | HPA and the SLO-aware Workload Variant Autoscaler |
| [Asynchronous Processing](../well-lit-paths/asynchronous-processing.md) | `guides/asynchronous-processing/` | Queue-based async inference *(experimental)* |
| [Batch Gateway](../well-lit-paths/experimental/batch-gateway.md) | `guides/batch-gateway/` | OpenAI-compatible Batch API *(experimental)* |

Benchmarks are driven by the [llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) framework. Each guide ships baseline and tuned configurations so you can reproduce the numbers published with the release.

---

## 5. Gateways

Gateways are **optional**. By default, the well-lit path guides run in **Standalone Mode** with the Standalone Router chart (Envoy + EPP), which does not require a Gateway provider.

You only need a Gateway provider if you want to:

- Integrate llm-d with an existing Kubernetes Gateway
- Terminate TLS at the edge or share a single ingress across services
- Run multi-tenant routing under a single Gateway

### Tested Gateway Providers (v0.7.0)

These are the versions we test against for the v0.7.0 release.

| Dependency | Tested Versions | Notes |
|------------|-----------------|-------|
| Gateway API CRDs | v1.5.x | Kubernetes SIG (required if using a Gateway) |
| Gateway API Inference Extension CRDs | v1.4.x | Kubernetes SIG (always required — see [section 1](#1-crds)) |
| Istio | 1.29.x | Default gateway provider |
| AgentGateway | v1.0.x | Preferred for new deployments |
| kgateway | v2.2.x | **Deprecated** — will be removed in the next release |

Gateway-specific install instructions live under [`guides/prereq/gateways/`](https://github.com/llm-d/llm-d/tree/main/guides/prereq/gateways) (Istio, AgentGateway, GKE).

---

## Source Repositories

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d](https://github.com/llm-d/llm-d) | — | Main repo: docs, Dockerfiles, guides, CI |
| [llm-d/llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) | Go | EPP routing engine and P/D sidecar |
| [llm-d/llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) | Go | KV-cache block locality indexer |
| [llm-d/llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) | Go | GPU-free vLLM simulator |
| [llm-d/llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) | Python | Benchmarking framework |
| [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | Go | SLO-aware workload autoscaler |
| [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) | — | CRDs and standalone/inferencepool Helm charts |
