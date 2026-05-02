# Artifacts

This page provides an inventory of all llm-d release artifacts including container images, Helm charts, Kustomize overlays, and key upstream dependencies. All versions listed correspond to the **v0.7.0** release.

## Container Images

llm-d publishes container images to the GitHub Container Registry (`ghcr.io/llm-d/`).

### Model Server Images

These images bundle vLLM and SGLang (CUDA only) with the libraries required for llm-d's well-lit paths (NIXL, DeepEP, etc.).

| Image | Accelerator | Base OS | Architectures | Status |
|-------|-------------|---------|---------------|--------|
| `ghcr.io/llm-d/llm-d-cuda:v0.7.0` | NVIDIA CUDA | RHEL UBI9 | amd64, arm64 | Available |
| `ghcr.io/llm-d/llm-d-cuda:v0.7.0-debug` | NVIDIA CUDA | RHEL UBI9 | amd64 | Available |
| `ghcr.io/llm-d/llm-d-aws` | NVIDIA CUDA + EFA | RHEL UBI9 | amd64, arm64 | Available |
| `ghcr.io/llm-d/llm-d-rocm` | AMD ROCm | RHEL UBI9 | amd64 | Available |
| `ghcr.io/llm-d/llm-d-xpu` | Intel XPU | Ubuntu 24.04 | amd64 | Available |
| `ghcr.io/llm-d/llm-d-hpu` | Intel Gaudi HPU | Ubuntu 22.04 | amd64 | Available |
| `ghcr.io/llm-d/llm-d-cpu` | CPU | RHEL UBI9 | amd64 | Available |

> The project is implementing a move to consuming upstream vLLM images directly, which would reduce the number of maintained images to a thin addon layer for llm-d-specific components. See [#1112](https://github.com/llm-d/llm-d/issues/1112).

### Sidecar and Infrastructure Images

| Image | Description | Version |
|-------|-------------|---------|
| `ghcr.io/llm-d/llm-d-inference-scheduler` | EPP — the inference-aware request router | v0.8.0 |
| `ghcr.io/llm-d/llm-d-routing-sidecar` | P/D disaggregation sidecar for KV transfer coordination | v0.8.0 |
| `ghcr.io/llm-d/llm-d-uds-tokenizer` | Unix domain socket tokenizer sidecar | v0.8.0 |
| `ghcr.io/llm-d/llm-d-kv-cache` | KV-cache block locality indexer library | v0.7.1 |
| `ghcr.io/llm-d/llm-d-inference-sim` | GPU-free vLLM simulator for testing | v0.8.2 |
| `ghcr.io/llm-d/llm-d-workload-variant-autoscaler` | SLO-aware workload autoscaler | v0.7.0 |
| `ghcr.io/llm-d/llm-d-rdma-tools` | RDMA diagnostic and testing utilities | v0.7.0 |

### Image Tags

| Tag Pattern | Meaning |
|-------------|---------|
| `v0.7.0` | Release tag — pinned, immutable |
| `latest` | Latest build from `main` — rolling |
| `sha-<short>` | Specific commit build |
| `pr-<number>` | Build from a pull request (dev only) |

> Development images use the `-dev` suffix in the image name (e.g., `llm-d-cuda-dev`). Release images drop the suffix.

## Deployment Manifests

llm-d uses two deployment methods depending on the guide. Both produce the same Kubernetes resources. The project is migrating to kustomize-first installation ([#850](https://github.com/llm-d/llm-d/issues/850)).

### Helm Charts

| Chart | Version | Registry | Repository | Notes |
|-------|---------|----------|------------|-------|
| **Standalone Router** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone` | [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) | Standalone Router (EPP+Envoy sidecar) |
| **InferencePool** | v1.5.0 | `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool` | [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) | InferencePool + EPP |
| **workload-variant-autoscaler** | v0.7.0 | `https://llm-d.github.io/llm-d-workload-variant-autoscaler/` | [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | Optional: SLO-aware autoscaling |
| **async-processor** | v0.6.1 | TBD | TBD | Optional: Queue-based async inference |
| **llm-d-infra** | v1.4.0 | `https://llm-d-incubation.github.io/llm-d-infra/` | [llm-d-incubation/llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra) | (Deprecated) Core infrastructure (gateway, CRDs). Used in legacy helmfile based guides prior to llm-d v0.7. |
| **llm-d-modelservice** | v0.4.9 | `https://llm-d-incubation.github.io/llm-d-modelservice/` | [llm-d-incubation/llm-d-modelservice](https://github.com/llm-d-incubation/llm-d-modelservice) | (Deprecated) Model server deployment. Used in legacy helmfile based guides prior to llm-d v0.7. |


### Kustomize Overlays

Used by the kustomize-based guides (tiered prefix cache, wide expert-parallelism). Reusable base layers live in `guides/recipes/`.

| Recipe | Path | Description |
|--------|------|-------------|
| **Gateway** | `guides/recipes/gateway/` | Base gateway manifest with provider overlays (Istio, AgentGateway, GKE, kgateway) |
| **InferencePool** | `guides/recipes/scheduler/` | InferencePool + EPP deployment |
| **vLLM** | `guides/recipes/modelserver/` | Model server base with standard overlay |

Each kustomize-based guide composes these recipes with guide-specific overlays for the target accelerator and infrastructure provider.

## Gateway Provider Dependencies

By default, llm-d guides runs a standalone router without a gateway. For gateway mode, the following gateway dependencies
are tested and supported versions for the v0.7.0 release.

| Dependency | Supported Versions | Notes |
|------------|-------------------|-------|
| Gateway API CRDs | v1.5.x | Kubernetes SIG |
| Gateway API Inference Extension CRDs | v1.4.x | Kubernetes SIG |
| Istio | 1.29.x | Default gateway provider |
| AgentGateway | v1.0.x | Preferred for new deployments |
| kgateway | v2.2.x | **Deprecated** — will be removed in next release |

## Key Upstream Dependencies

Exact versions pinned in the v0.7.0 container images. See [upstream-versions.md](https://github.com/llm-d/llm-d/blob/main/docs/upstream-versions.md) for the authoritative source.

| Dependency | Pinned Version (v0.7.0) | Purpose |
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

### Hardware-Specific vLLM Variants

| Variant | Version | Upstream |
|---------|---------|----------|
| vLLM Gaudi (HPU) | 1.22.0 | [HabanaAI/vllm-fork](https://github.com/HabanaAI/vllm-fork) |
| vLLM TPU | v0.13.2-ironwood | [vllm-project/vllm](https://github.com/vllm-project/vllm) |

## Source Repositories

| Repository | Language | Description |
|------------|----------|-------------|
| [llm-d/llm-d](https://github.com/llm-d/llm-d) | — | Main repo: docs, Dockerfiles, guides, CI |
| [llm-d/llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) | Go | EPP routing engine and P/D sidecar |
| [llm-d/llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) | Go | KV-cache block locality indexer |
| [llm-d/llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) | Go | GPU-free vLLM simulator |
| [llm-d/llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) | Python | Benchmarking framework |
| [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | Go | SLO-aware workload autoscaler |
| [llm-d-incubation/llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra) | Helm | Infrastructure chart (gateway, CRDs) |
| [llm-d-incubation/llm-d-modelservice](https://github.com/llm-d-incubation/llm-d-modelservice) | Helm | Model server deployment chart |
