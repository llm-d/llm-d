# Introduction to llm-d

llm-d is a high-performance distributed inference serving stack optimized for production deployments on Kubernetes. We help you achieve the fastest "time to state-of-the-art (SOTA) performance" for key OSS large language models across most hardware accelerators and infrastructure providers with well-tested guides and real-world benchmarks.

## Why llm-d?

### Distributed Performance Optimization

While model servers like [vLLM](https://github.com/vllm-project/vllm) and [SGLang](https://github.com/sgl-project/sglang) optimize individual nodes, llm-d orchestates multiple model server pods to implement key distribtued optimizations to serve at scale, including LLM-aware load balancing and request prioritization, distributed KV caching, disaggregated serving, multi-node "wide EP", and autoscaling — so you can serve high-scale production traffic efficiently and reliably.

### Vendor-Neutral and Engine-Agnostic

llm-d is a [CNCF sandbox project](https://www.cncf.io/) that supports multiple inference engines (vLLM, SGLang) and multiple hardware backends (NVIDIA, AMD, Google TPU, Intel HPU) following an open development model.

### Kubernetes-Native

llm-d integrates with standard Kubernetes primitives — Gateway API, Custom Resources, Labels, and HPA — rather than introducing a new orchestration layers or CRDs. If you already run workloads on Kubernetes, llm-d fits naturally into your infrastructure.

## Key Capabilities

### optimized baseline

LLM-aware load balancing that goes beyond naive round-robin. The LLM-aware load balancing uses a plugin-based scoring pipeline to route each request to the optimal model server replica based on:

- **Prefix cache locality** — route to replicas that already have relevant KV-cache entries
- **KV-cache utilization** — prefer replicas with more available memory
- **Queue depth** — avoid overloading busy replicas
- **Predicted latency** — SLO-aware routing based on live traffic patterns (experimental)

This alone can deliver order-of-magnitude latency reductions vs. round-robin baselines.

### Prefill/Decode Disaggregation

Split inference into dedicated **prefill workers** (prompt processing) and **decode workers** (token generation) to reduce time-to-first-token (TTFT) and achieve more predictable time-per-output-token (TPOT). KV-cache is transferred between phases via [NIXL](https://github.com/ai-dynamo/nixl) over high-speed interconnects (InfiniBand, RoCE RDMA).

### Wide Expert-Parallelism

Deploy large Mixture-of-Experts models like DeepSeek-R1 across multiple nodes using combined Data Parallelism and Expert Parallelism deployments. This deployment pattern maximizes KV cache space for large models, enabling long-context online serving and high-throughput generation for batch and RL use cases.

### Tiered KV Prefix Caching

Extend prefix cache capacity beyond accelerator HBM by offloading KV-cache entries through a configurable storage hierarchy:

- **Accelerator HBM** — fastest, limited capacity
- **CPU memory** — fast transfer, larger capacity
- **Local SSD** — cost-effective, higher latency
- **Remote filesystem** — durable, shareable across replicas (in progress)

### Workload Autoscaling

Two complementary autoscaling patterns:

- **HPA with Inference Gateway metrics** — Kubernetes-native scaling based on queue depth and request counts from the EPP
- **Workload Variant Autoscaler** — multi-model, SLO-aware scaling on heterogeneous hardware that optimizes cost by routing across model variants

## Architecture at a Glance

llm-d uses a layered, composable architecture:

![Architecture](../../assets/basic-architecture.svg)

| Component | Role |
|---|---|
| **[Proxy](../architecture/core/proxy.md)** | Deployed via Kubernetes Gateway API or as a standalone GAIE-conformant proxy. |
| **[Endpoint Picker (EPP)](../architecture/core/epp/README.md)** | The scheduling brain — scores and selects the optimal backend for each request using a plugin pipeline of filters, scorers, and pickers. |
| **[InferencePool](../architecture/core/inferencepool.md)** | A Kubernetes Custom Resource that groups model server pods sharing the same model and compute configuration. |
| **[Model Servers](../architecture/core/model-servers.md)** | vLLM or SGLang instances running models on accelerators. |

See the [Architecture Overview](../architecture/README.md) for a deeper dive into the architecture.

## Well-Lit Paths

In addition to the software components, llm-d provides **Well-Lit Paths** — tested, benchmarked deployment recipes for common production patterns. These paths are starting points designed to be adapted for your models, hardware, and traffic patterns.

Each path includes:
- Deployable Helm charts and Kustomize manifests
- Key configuration knobs for performance tuning
- Sample workloads and benchmarks against baseline setups
- Monitoring and observability configuration

See the [Well-Lit Paths Guides](../well-lit-paths/README.md) for more details on how to deploy.
