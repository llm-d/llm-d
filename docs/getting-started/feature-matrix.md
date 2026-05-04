# Feature Matrix

llm-d supports multiple model servers, accelerator backends, and infrastructure providers at various levels of maturity.

This page describes the current coverage as validated in the v0.7.0 release and nightly CI.

## Well-Lit Paths × Model Server × Accelerator

### Optimized Baseline

| Accelerator | vLLM | SGLang |
|-------------|------|--------|
| NVIDIA CUDA | ✅ | ✅ |
| AMD ROCm | ✅ | — |
| Intel XPU | ✅ | — |
| Intel Gaudi (HPU) | ✅ | — |
| Google TPU | ✅ | — |
| CPU | ✅ | — |

**Nightly CI**: OpenShift (CUDA), GKE (CUDA), CoreWeave (CUDA), XPU (PR-triggered), HPU (PR-triggered)

### Precise Prefix-Cache-Aware Routing

| Accelerator | vLLM | SGLang |
|-------------|------|--------|
| NVIDIA CUDA | ✅ | ✅ |
| Intel XPU | ✅ | — |

**Nightly CI**: OpenShift (CUDA), CoreWeave (CUDA), GKE (CUDA)

### Prefill/Decode Disaggregation

| Accelerator | vLLM | SGLang |
|-------------|------|--------|
| NVIDIA CUDA | ✅ | ✅ |
| AMD ROCm | ✅ | — |
| Intel XPU | ✅ | — |
| Google TPU | ✅ | — |

**Nightly CI**: OpenShift (CUDA), GKE (CUDA), CoreWeave (CUDA)

### Wide Expert-Parallelism

| Accelerator | vLLM | SGLang |
|-------------|------|--------|
| NVIDIA CUDA | ✅ | — |

**Nightly CI**: OpenShift (CUDA), GKE (CUDA), CoreWeave (CUDA)

> Requires LeaderWorkerSet (LWS) for multi-node orchestration. DP-aware scheduling variant is under development.

### Tiered Prefix Cache

| Accelerator | CPU Offload (vLLM) | Storage Offload (vLLM) | SGLang |
|-------------|--------------------|-----------------------|--------|
| NVIDIA CUDA | ✅ | ✅ | — |
| Intel XPU | — | — | — |
| Google TPU | Coming soon | — | — |

**Nightly CI**: OpenShift (CUDA)

### Workload Autoscaling

| Variant | vLLM | SGLang |
|---------|------|--------|
| HPA + IGW Metrics | ✅ | — |
| Workload Variant Autoscaler (WVA) | ✅ | — |

**Nightly CI**: OpenShift (WVA), CoreWeave (WVA)

### Predicted Latency-Based Scheduling

| Accelerator | vLLM | SGLang |
|-------------|------|--------|
| NVIDIA CUDA | ✅ | ✅ |

**Nightly CI**: OpenShift (CUDA), GKE (CUDA), CoreWeave (CUDA)

> Accelerator-agnostic: only validated on NVIDIA CUDA, but the scheduler logic does not depend on accelerator type and should work on any backend supported by vLLM or SGLang.

### Asynchronous Processing

| Backend | vLLM | SGLang |
|---------|------|--------|
| Redis | ✅ | — |
| GCP Pub/Sub | ✅ | — |

**Nightly CI**: None

### Batch Gateway

| Backend | vLLM | SGLang |
|---------|------|--------|
| PostgreSQL + Redis | ✅ | — |
| S3 + Redis | ✅ | — |

**Nightly CI**: None

> Provides OpenAI-compatible Batch API (`/v1/batches`, `/v1/files`) for offline inference workloads.

## Infrastructure Providers

| Provider | Optimized Baseline | P/D Disaggregation | Wide EP | Tiered Prefix Cache | Precise Prefix Cache | WVA |
|----------|---------------------|--------------------|---------|--------------------|-----------------------|-----|
| **OpenShift** | Nightly | Nightly | Nightly | Nightly | Nightly | Nightly |
| **GKE** | Nightly | Nightly | Nightly | — | Nightly | — |
| **CoreWeave (CKS)** | Nightly | Nightly | Nightly | — | Nightly | Nightly |
| **Minikube** | Manual | — | — | — | — | — |
| **DigitalOcean** | Manual | — | — | — | — | — |
| **AKS** | Manual | — | — | — | — | — |

## Gateway Providers

| Provider | Status | Notes |
|----------|--------|-------|
| **Istio** | Default | Used in all well-lit paths |
| **AgentGateway** | Supported | Preferred for new self-installed deployments |
| **GKE Gateway** | Supported | Externally managed, used in GKE guides |
| **kgateway** | Deprecated | Will be removed in next release |

## Support Matrix

### Supported Hardware

For accelerator maintainer contacts and contribution requirements, see [Accelerator Support](../../accelerators/README.md). The information below is also maintained in that document and will be consolidated into this feature matrix in a future docs revision.

| Accelerator | Supported Devices | Notes |
|-------------|-------------------|-------|
| NVIDIA CUDA | A100, H100, H200, B200 | Primary platform. All well-lit paths validated. |
| AMD ROCm | MI250, MI300X | Optimized baseline and P/D disaggregation. |
| Google TPU | v5e, v6e, v7 | GKE only. P/D and optimized baseline. |
| Intel XPU | Data Center GPU Max 1550, BMG (Battlemage) | Uses DRA. Opitmized baseline, P/D, precise prefix cache. |
| Intel Gaudi (HPU) | Gaudi 2, Gaudi 3 | Uses DRA. Optimized baseline. |
| CPU | Intel Xeon (Sapphire Rapids+), AMD EPYC | 64 cores, 64 GB RAM per replica. |

### Software Requirements

| Component | Minimum Version | Notes |
|-----------|-----------------|-------|
| Kubernetes | 1.30+ | Gateway API v1 support required |
| Gateway API CRDs | v1.5.1 | |
| Gateway API Inference Extension CRDs | v1.5.0 | |
| Helm | 3.x | For helmfile-based guides |
| Helmfile | 0.x | For helmfile-based guides |
| kubectl | 1.30+ | |
| kustomize | 5.x | For kustomize-based guides (tiered prefix cache, wide EP) |

### Installation Methods

llm-d guides use two deployment methods. Both produce the same Kubernetes resources.

| Method | Notes |
|--------|-------|
| **Helm** | Used to deploy llm-d router in standalone and gateway modes, async processor, etc. |
| **Kustomize** | Used to deploy declarative overlays for model servers, gateways, etc. Reusable base layers in `guides/recipes/`. |

> The project is migrating from helmfile to kustomize-first installation ([tracking issue](https://github.com/llm-d/llm-d/issues/850)). New guides should prefer kustomize.

## Guide Maturity

Each well-lit path guide is assigned a maturity level reflecting its testing and documentation coverage.

| Level | Definition |
|-------|------------|
| **High** | Tested nightly across multiple infrastructure providers (OpenShift, GKE, CoreWeave). Benchmarked and documented. |
| **Medium** | Tested nightly on at least one infrastructure provider. Documented with deployment guide. |
| **Experimental** | Functional but not regularly tested by maintainers. May have known limitations. |

| Guide | Maturity | Nightly Providers |
|-------|----------|-------------------|
| Optimized Baseline (vLLM, CUDA) | High | OpenShift, GKE, CoreWeave |
| Optimized Baseline (SGLang, CUDA) | Medium | — |
| Optimized Baseline (AMD, XPU, HPU, TPU, CPU) | Experimental | XPU, HPU (PR-triggered) |
| Precise Prefix-Cache-Aware Routing | Medium | OpenShift |
| Prefill/Decode Disaggregation (vLLM, CUDA) | High | OpenShift, GKE, CoreWeave |
| Prefill/Decode Disaggregation (SGLang, CUDA) | Experimental | — |
| Prefill/Decode Disaggregation (AMD, XPU, TPU) | Experimental | — |
| Wide Expert-Parallelism | Experimental | OpenShift, GKE, CoreWeave |
| Tiered Prefix Cache | Medium | OpenShift |
| Simulated Accelerators | Medium | OpenShift |
| Workload Autoscaling (WVA) | Experimental | OpenShift, CoreWeave |
| Workload Autoscaling (HPA + IGW) | Experimental | — |
| Predicted Latency-Based Scheduling | Medium | OpenShift, GKE, CoreWeave |
| Asynchronous Processing | Experimental | — |
