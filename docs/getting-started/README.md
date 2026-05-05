# Introduction to llm-d

llm-d is a high-performance distributed inference serving stack optimized for production deployments on Kubernetes. We help you achieve the fastest "time to state-of-the-art (SOTA) performance" for key OSS large language models across most hardware accelerators and infrastructure providers with well-tested guides and real-world benchmarks.

## Why llm-d?

### Distributed Performance Optimization

While model servers like [vLLM](https://github.com/vllm-project/vllm) and [SGLang](https://github.com/sgl-project/sglang) optimize individual nodes, llm-d orchestrates multiple model server pods to implement key distributed optimizations to serve at scale, including LLM-aware load balancing and request prioritization, distributed KV caching, disaggregated serving, multi-node "wide EP", and autoscaling — so you can serve high-scale production traffic efficiently and reliably.

### Vendor-Neutral and Engine-Agnostic

llm-d is a [CNCF sandbox project](https://www.cncf.io/) that supports multiple inference engines (vLLM, SGLang) and multiple hardware backends (NVIDIA, AMD, Google TPU, Intel HPU) following an open development model.

### Kubernetes-Native

llm-d integrates with standard Kubernetes primitives — Gateway API, Custom Resources, Labels, and HPA. If you already run workloads on Kubernetes, llm-d fits naturally into your infrastructure.

### Composable and Incremental Adoption

While llm-d offers a comprehensive suite of state-of-the-art optimizations, its architecture is fundamentally composable. You do not need to "buy into" the entire stack to benefit from individual features. Whether you only need intelligent, prefix-aware load balancing for an existing pool of model servers, or you want to layer in disaggregated serving and proactive autoscaling, llm-d allows you to incrementally adopt specific capabilities that solve your immediate production bottlenecks.

## Architecture at a Glance

llm-d uses a layered, composable architecture. See the [Architecture Overview](../architecture/README.md) for a deeper dive into the architecture.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Arch" src="../assets/images/llm-d-arch.svg" width=95%>
  </picture>
</p>

## Key Capabilities

### llm-d Router (Intelligent Load Balancing)

The core of llm-d is the **llm-d Router**, which provides LLM-aware load balancing that goes beyond naive round-robin. The router is split into two functional components: an industry-grade **Proxy** (like Envoy) that handles the data plane, and llm-d's **Endpoint Picker (EPP)** that provides the routing intelligence.

The EPP component uses a pluggable scoring pipeline to route each request to the optimal model server replica based on:

- **Prefix cache locality** — route to replicas that already have relevant KV-cache entries
- **KV-cache utilization** — prefer replicas with more available memory
- **Queue depth** — avoid overloading busy replicas

This alone can deliver order-of-magnitude latency reductions vs. round-robin baselines.

### KV Cache Management

A comprehensive ecosystem for managing and reusing the KV cache across the inference pool. This includes:

- **Prefix-Cache Aware Routing** — The **llm-d Router** (via the EPP) routes requests to replicas that already contain relevant KV-cache entries, eliminating redundant prefill computation and reducing latency.
- **KV-Cache Indexing** — Maintains a real-time, globally consistent view of exactly which token blocks reside on which model server replicas using high-frequency event tracking.
- **KV Offloading** — Extends cache capacity beyond accelerator HBM by offloading KV-cache entries through a storage hierarchy (CPU memory, local SSD).

By composing these layers, llm-d allows an inference pool to scale its effective cache capacity far beyond physical hardware limits.

### Prefill/Decode Disaggregation

Split inference into dedicated **prefill workers** (prompt processing) and **decode workers** (token generation) to reduce time-to-first-token (TTFT) and achieve more predictable time-per-output-token (TPOT). KV-cache is transferred between phases via [NIXL](https://github.com/ai-dynamo/nixl) over high-speed interconnects (InfiniBand, RoCE RDMA).

### Predicted Latency-Based Routing

An **llm-d Router** capability implemented primarily as an EPP plugin that routes each request to the replica predicted to serve it fastest, using an XGBoost model trained online on live traffic to predict ITL and TTFT. Optionally enforce per-request SLOs via `x-slo-ttft-ms` / `x-slo-tpot-ms` headers — requests that no replica can meet within budget are shed at admission rather than routed to a guaranteed miss. Useful when workload variance makes queue-depth a poor proxy for true load, or when clients need to express interactive vs. batch latency budgets.

### Batch Inference

Manage high-volume offline inference workloads through OpenAI-compatible Batch APIs. llm-d provides two primary components for batch processing:
- **llm-d Batch Gateway** — Provides `/v1/batches` and `/v1/files` endpoints for job submission and tracking.
- **llm-d Async Processor** — A dispatch agent that pulls requests from message queues (such as Redis and Google Pub/Sub) and feeds them to the llm-d Router with system-aware flow control to prevent impacting interactive traffic.

### Flow Control

The **llm-d Router** implements sophisticated flow control to protect the inference pool from saturation and ensure fair resource sharing across tenants. By shifting queuing from isolated model server queues to a centralized, policy-aware buffer in the EPP, llm-d provides:

- **Multi-Tenant Fairness** — Prevents "noisy neighbors" from monopolizing GPU capacity by enforcing fairness policies (e.g., Round-Robin) across different tenant IDs.
- **Strict Priority Dispatch** — Ensures that high-priority interactive requests are always serviced before lower-priority background tasks, even during periods of heavy load.
- **Late-Binding Scheduling** — Delays routing decisions until the moment capacity becomes available, ensuring requests are matched to the best possible replica (e.g., one with the highest prefix-cache affinity).
- **Proactive SLO Guardrails** — Optionally rejects or sheds traffic that cannot meet latency targets, protecting the Quality of Service (QoS) for admitted requests.

This centralized queuing model also provides a definitive "True Demand" metric, enabling more proactive and accurate autoscaling than traditional GPU utilization metrics alone.

### Wide Expert-Parallelism

Deploy large Mixture-of-Experts models like DeepSeek-R1 across multiple nodes using combined Data Parallelism and Expert Parallelism deployments. This deployment pattern maximizes KV cache space for large models, enabling long-context online serving and high-throughput generation for batch and RL use cases.

### Workload APIs for Multi-Node Serving

The project drives the evolution of multi-node and disaggregated workload orchestration via Kubernetes-native APIs that simplify the management of complex, multi-container inference workloads:

- **LeaderWorkerSet (LWS)** — Optimized for multi-node distributed inference (e.g., Wide Expert Parallelism). It manages a group of pods as a single logical unit, where a "leader" pod coordinates one or more "worker" pods, providing common network identity and lifecycle management for collective operations.
- **DisaggregatedSet** — Specifically designed for disaggregated serving architectures. It manages the lifecycle and scaling of heterogeneous component sets (like Prefill and Decode server pods) that must work together, ensuring that related resources are provisioned and connected efficiently.

### Workload Autoscaling

Two complementary autoscaling patterns:

- **HPA with llm-d Router metrics** — Kubernetes-native scaling based on queue depth and request counts from the Router's internal metrics.
- **Workload Variant Autoscaler** — multi-model, SLO-aware scaling on heterogeneous hardware that optimizes cost by routing across model variants.

## Well-Lit Paths

In addition to the software components, llm-d provides **Well-Lit Paths** — tested, benchmarked deployment recipes for common production patterns. These paths are starting points designed to be adapted for your models, hardware, and traffic patterns.

Each path includes:
- Deployable Helm charts and Kustomize manifests
- Key configuration knobs for performance tuning
- Sample workloads and benchmarks against baseline setups
- Monitoring and observability configuration

See the [Well-Lit Paths Guides](../well-lit-paths/README.md) for more details on how to deploy.
