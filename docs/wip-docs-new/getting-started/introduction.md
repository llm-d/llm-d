# Introduction to llm-d

llm-d is a high-performance distributed inference serving stack for large language models, optimized for production deployments on Kubernetes. It bridges the gap between single-node inference engines and the demands of real-world serving at scale.

> **Inference engines optimize the accelerator; llm-d optimizes the system around them.**

While model servers like [vLLM](https://docs.vllm.ai) and [SGLang](https://github.com/sgl-project/sglang) handle running models efficiently on individual accelerators, llm-d provides the orchestration layer above them — intelligent scheduling, distributed caching, disaggregated serving, and autoscaling — so you can serve high-scale production traffic efficiently and reliably.

## Why llm-d?

### System-Level Optimization

Achieving state-of-the-art inference performance requires more than a fast model server. Real-world serving involves routing requests to the right replica, managing KV-cache across tiers of memory, splitting prefill and decode phases across dedicated hardware, and autoscaling to meet SLOs. llm-d handles all of this as a composable set of production-tested components.

### Vendor-Neutral and Engine-Agnostic

llm-d is a [CNCF sandbox project](https://www.cncf.io/) that supports multiple inference engines (vLLM, SGLang) and multiple hardware backends (NVIDIA, AMD, Google TPU, Intel HPU). There are no proprietary forks — the team contributes optimizations directly to upstream projects like vLLM and the Kubernetes Gateway API.

### Kubernetes-Native

llm-d integrates with standard Kubernetes primitives — Gateway API, Custom Resources, Labels, and HPA — rather than introducing a proprietary orchestration layer. If you already run workloads on Kubernetes, llm-d fits naturally into your infrastructure.

### Modular Adoption

You don't need to adopt everything at once. Start with intelligent scheduling for an immediate latency improvement, then layer on disaggregated serving, tiered caching, or autoscaling as your needs grow. Each capability is an independent, composable component.

## Design Principles

| Principle | Description |
|---|---|
| **Keep it simple** | Well-lit paths reduce operational complexity with tested, benchmarked recipes |
| **Composition over configuration** | Components connect at clean API boundaries and can be mixed and matched |
| **Respect upstreams** | vLLM and Gateway API are the source of truth — no proprietary forks |
| **Production-ready** | High standards for review, testing, and reliability for core components |
| **Move fast** | Experimental features are encouraged — opt-in and isolated from stable paths |

## Key Capabilities

### Intelligent Inference Scheduling

LLM-aware load balancing that goes beyond round-robin. The [Endpoint Picker (EPP)](../architecture/core/epp.md) uses a plugin-based scoring pipeline to route each request to the optimal model server replica based on:

- **Prefix cache locality** — route to replicas that already have relevant KV-cache entries
- **KV-cache utilization** — prefer replicas with more available memory
- **Queue depth** — avoid overloading busy replicas
- **Predicted latency** — SLO-aware routing based on live traffic patterns (experimental)

This alone can deliver order-of-magnitude latency reductions vs. round-robin baselines.

### Prefill/Decode Disaggregation

Split inference into dedicated **prefill workers** (prompt processing) and **decode workers** (token generation) to reduce time-to-first-token (TTFT) and achieve more predictable time-per-output-token (TPOT). KV-cache is transferred between phases via [NIXL](https://github.com/ai-dynamo/nixl) over high-speed interconnects (InfiniBand, RoCE RDMA, TPU ICI).

### Wide Expert-Parallelism

Deploy large Mixture-of-Experts models like DeepSeek-R1 across dozens of accelerators using combined Data Parallelism and Expert Parallelism. Validated at 50k+ output tokens/sec on a 16x16 B200 topology for throughput-optimized workloads like RL post-training.

### Tiered KV Prefix Caching

Extend prefix cache capacity beyond accelerator HBM by offloading KV-cache entries through a configurable storage hierarchy:

- **Accelerator HBM** — fastest, limited capacity
- **CPU memory** — fast transfer, larger capacity
- **Local SSD** — cost-effective, higher latency
- **Remote filesystem** — durable, shareable across replicas

### Workload Autoscaling

Two complementary autoscaling patterns:

- **HPA with Inference Gateway metrics** — Kubernetes-native scaling based on queue depth and request counts from the EPP
- **Workload Variant Autoscaler** — multi-model, SLO-aware scaling on heterogeneous hardware that optimizes cost by routing across model variants

## Architecture at a Glance

llm-d uses a layered, composable architecture:

```
Client Request → Proxy → Endpoint Picker (EPP) → InferencePool → Model Servers → Accelerators
```

| Layer | Role |
|---|---|
| **[Proxy](../architecture/core/proxy.md)** | Ingress via Kubernetes Gateway API or standalone Envoy. Exposes OpenAI-compatible endpoints. |
| **[Endpoint Picker (EPP)](../architecture/core/epp.md)** | The scheduling brain — scores and selects the optimal backend for each request using a plugin pipeline of filters, scorers, and pickers. |
| **[InferencePool](../architecture/core/inferencepool.md)** | A Kubernetes Custom Resource that groups model server pods sharing the same model and compute configuration. |
| **[Model Servers](../architecture/core/model-servers.md)** | vLLM or SGLang instances running models on accelerators, exposing metrics for scheduling decisions. |

For a deeper dive, see the [Architecture Overview](../architecture/introduction.md).

## Well-Lit Paths

llm-d provides **Well-Lit Paths** — tested, benchmarked deployment recipes for common production patterns. Each path includes:

- Deployable Helm charts and Kustomize manifests
- Key configuration knobs for performance tuning
- Sample workloads and benchmarks against baseline setups
- Monitoring and observability configuration

These paths are starting points designed to be adapted for your models, hardware, and traffic patterns. See the [Feature Matrix](feature-matrix.md) for current engine and accelerator coverage.

## What's Next?

<div class="grid cards" markdown>

- **Quickstart** — Deploy llm-d with vLLM in minutes
    - [Standalone mode](quickstart/standalone.md) — lightweight Envoy proxy, ideal for getting started
    - [Gateway API mode](quickstart/gateway.md) — full Kubernetes Gateway API integration

- **[Architecture Overview](../architecture/introduction.md)** — Understand the core components and how they fit together

- **[Feature Matrix](feature-matrix.md)** — See which capabilities are validated on your engine and hardware

- **[Artifacts](artifacts.md)** — Container images, Helm charts, and release inventory

</div>
