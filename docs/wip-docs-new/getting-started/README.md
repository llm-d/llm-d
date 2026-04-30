# Introduction to llm-d

llm-d is a high-performance distributed inference serving stack optimized for production deployments on Kubernetes. We help you achieve the fastest "time to state-of-the-art (SOTA) performance" for key OSS large language models across most hardware accelerators and infrastructure providers with well-tested guides and real-world benchmarks.

## Why llm-d?

### Distributed Performance Optimization

While model servers like [vLLM](https://github.com/vllm-project/vllm) and [SGLang](https://github.com/sgl-project/sglang) optimize individual nodes, llm-d orchestrates multiple model server pods to implement key distributed optimizations to serve at scale, including LLM-aware load balancing and request prioritization, distributed KV caching, disaggregated serving, multi-node "wide EP", and autoscaling — so you can serve high-scale production traffic efficiently and reliably.

### Vendor-Neutral and Engine-Agnostic

llm-d is a [CNCF sandbox project](https://www.cncf.io/) that supports multiple inference engines (vLLM, SGLang) and multiple hardware backends (NVIDIA, AMD, Google TPU, Intel HPU) following an open development model.

### Kubernetes-Native

llm-d integrates with standard Kubernetes primitives — Gateway API, Custom Resources, Labels, and HPA — rather than introducing new orchestration layers or CRDs. If you already run workloads on Kubernetes, llm-d fits naturally into your infrastructure.

## Key Capabilities

### llm-d Router (Intelligent Load Balancing)

The core of llm-d is the **llm-d Router**, which provides LLM-aware load balancing that goes beyond naive round-robin. The router is split into two functional components: an industry-grade **Proxy** (like Envoy) that handles the data plane, and llm-d's **Endpoint Picker (EPP)** that provides the routing intelligence.

The EPP component uses a pluggable scoring pipeline to route each request to the optimal model server replica based on:

- **Prefix cache locality** — route to replicas that already have relevant KV-cache entries
- **KV-cache utilization** — prefer replicas with more available memory
- **Queue depth** — avoid overloading busy replicas

This alone can deliver order-of-magnitude latency reductions vs. round-robin baselines.

### Predicted Latency-Based Routing

An **llm-d Router** capability implemented primarily as an EPP plugin that routes each request to the replica predicted to serve it fastest, using an XGBoost model trained online on live traffic. Optionally enforce per-request SLOs via `x-slo-ttft-ms` / `x-slo-tpot-ms` headers — requests that no replica can meet within budget are shed at admission rather than routed to a guaranteed miss. Useful when workload variance makes queue-depth a poor proxy for true load, or when clients need to express interactive vs. batch latency budgets.

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

- **HPA with llm-d Router metrics** — Kubernetes-native scaling based on queue depth and request counts from the Router's internal metrics.
- **Workload Variant Autoscaler** — multi-model, SLO-aware scaling on heterogeneous hardware that optimizes cost by routing across model variants.

## Architecture at a Glance

llm-d uses a layered, composable architecture centered around the **llm-d Router**.

<p align="center">
  <img src="../../assets/basic-architecture.svg" width="600" alt="Architecture">
</p>

| Component | Role |
|---|---|
| **[llm-d Router](../architecture/core/router/README.md)** | The intelligent request gateway. It is composed of a **Proxy** (Envoy) and the **Endpoint Picker (EPP)**. The Router makes model-aware routing decisions, manages flow control, and enforces request-level policies. |
| **[InferencePool](../architecture/core/inferencepool.md)** | A Kubernetes Custom Resource that groups model server pods and defines how the Router discovers and interacts with them. |
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
