# Architecture

High-level guide to llm-d architecture. Start here, then dive into specific guides.

## Core Components

The llm-d architecture is built around three primary concepts: the [Router](core/router/README.md), the [InferencePool](core/inferencepool.md), and the [Model Server](core/model-servers.md).

- **[llm-d Router](core/router/README.md)** - The intelligent entry point for inference requests. It provides LLM-aware load balancing, request queuing, and policy enforcement. It is composed of two functional parts:
    - **[Proxy](core/router/proxy.md)**: A high-performance L7 proxy (typically Envoy) that accepts user requests and consults the EPP via the `ext-proc` protocol to determine the optimal destination.
    - **[Endpoint Picker (EPP)](core/router/epp/README.md)**: The routing engine that scores and selects model server pods based on real-time metrics, KV-cache affinity, and configured policies.

- **[InferencePool](core/inferencepool.md)** - The API that defines a group of Model Server Pods sharing the same model and compute configuration. Conceptualized as an "LLM-optimized Service", it serves as the discovery target for the Router.

- **[Model Server](core/model-servers.md)** - The inference engine (such as vLLM or SGLang) that executes the model on hardware accelerators (GPUs, TPUs, HPUs).

<p align="center">
  <img src="../assets/basic-architecture.svg" width="660" alt="Architecture">
</p>

## Advanced Patterns

llm-d's core design can be extended with optional advanced patterns:

### KV Cache Management

llm-d provides a comprehensive ecosystem for managing and reusing the KV cache across the inference pool. This includes:
- [Prefix-Cache Aware Routing](advanced/kv-management/prefix-cache-aware-routing.md): Heuristic and precise techniques to maximize cache hits.
- [KV-Cache Indexing](advanced/kv-management/kv-indexer.md): Event-driven tracking of cache state across all model servers.
- [KV Offloading](advanced/kv-management/kv-offloader.md): Tiered storage hierarchy (CPU, SSD) for extending cache capacity.

See [KV Cache Management](advanced/kv-management/README.md) for an overview of how these components compose.

### Disaggregated Serving

In disaggregated serving, a single inference request is split into multiple phases (e.g., Prefill and Decode) handled by specialized workers. The llm-d Router orchestrates this flow by selecting both a prefill and a decode endpoint and coordinating the KV-cache transfer between them.

See [Disaggregation](advanced/disaggregation/README.md) for complete details.

### Predicted Latency-Based Routing

The llm-d Router can be extended with "consultant" sidecars that provide advanced signals for routing decisions. The primary implementation is the **Latency Predictor**, which enables routing based on predicted ITL and TTFT.

- [Latency Predictor](advanced/latency-predictor.md): Trains an XGBoost model online to predict request latency for better endpoint scoring and SLO enforcement.

### Batch Inference

Batch and offline inference workloads are handled by two modules that can be deployed independently or together. The Batch Gateway provides an OpenAI-compatible Batch API for job management, while the Async Processor dispatches queued requests with flow-control gating. When composed, the Batch Gateway delegates dispatch to the Async Processor.

See [Batch Inference](advanced/batch/README.md) for details on the batch inference design.

### Autoscaling

llm-d supports proactive, SLO-aware autoscaling through two complementary approaches:
- **HPA/KEDA**: Standard Kubernetes-native scaling using metrics exported by the EPP (like queue depth).
- **Workload Variant Autoscaler (WVA)**: Globally optimized scaling that minimizes cost by routing traffic across different model variants (e.g., different hardware or quantization) while meeting latency targets.

See [Autoscaling](advanced/autoscaling/README.md) for complete details.
