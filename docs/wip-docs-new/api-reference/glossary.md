# Glossary

Quick-reference definitions for terms used throughout the llm-d documentation. For a high-level overview of how these pieces fit together, see the [Architecture Overview](../architecture/README.md).

---

**Aggregated Serving** — The default serving mode where a single Model Server handles both Prefill and Decode for each request, as opposed to Disaggregated Serving.

**Consultant** — An optional sidecar component that the EPP queries for additional scoring signals beyond built-in metrics. Examples include the [Latency Predictor](../architecture/advanced/latency-predictor.md) and the [KV-Cache Indexer](../architecture/advanced/kv-indexer.md).

**Data Parallelism (DP)** — Running independent model replicas on separate GPUs within the same node, each handling different requests for higher aggregate throughput. See [Model Servers](../architecture/core/model-servers.md).

**Decode** — The second phase of LLM inference that generates output tokens one at a time, each depending on the previous token's KV Cache state. Decode throughput is measured by TPOT. See [Architecture Overview](../architecture/README.md).

**Disaggregated Serving** — A deployment pattern that separates Prefill and Decode into dedicated, independently scalable pools of Model Servers, connected by NIXL for KV-cache transfer. See [Disaggregation](../architecture/advanced/disaggregation/README.md).

**Endpoint Picker (EPP)** — The central routing component of llm-d. Receives ext-proc callbacks from the Proxy, evaluates candidate Model Servers through a Plugin Pipeline of filters, scorers, and pickers, and returns the address of the optimal backend. See [EPP](../architecture/core/epp/README.md).

**Envoy** — A high-performance L7 GAIE-conformant proxy that can be used with llm-d as a data-plane Proxy. It communicates routing decisions with the EPP via ext-proc. See [Proxy](../architecture/core/proxy.md).

**Expert Parallelism (EP)** — Distributing the expert layers of MoE models across multiple GPUs, enabling large models like DeepSeek-R1 to be served across nodes. See [Model Servers](../architecture/core/model-servers.md).

**ext-proc (External Processing)** — An Envoy filter protocol that offloads per-request routing decisions to an external gRPC service — in llm-d, the EPP. This is the communication channel between the Proxy and the routing logic. See [EPP](../architecture/core/epp/README.md).

**Flow Control** — The EPP subsystem that manages admission, queuing, and dispatch of requests using a Priority, Fairness, and Ordering hierarchy to prevent backend overload. See [EPP](../architecture/core/epp/README.md).

**Gateway API** — The Kubernetes-native API for configuring L7 traffic routing, succeeding Ingress. llm-d uses Gateway API resources (`HTTPRoute`, `Gateway`) to route external traffic to InferencePools. See [Proxy](../architecture/core/proxy.md).

**Gateway API Inference Extension (GAIE)** — An extension to Gateway API that adds inference-aware routing via ext-proc. Defines the InferencePool CRD. See [Proxy](../architecture/core/proxy.md).

**InferencePool** — A Kubernetes custom resource, defined by the Gateway API Inference Extension, that represents the set of Model Server pods an EPP considers when routing a request. See [InferencePool](../architecture/core/inferencepool.md).

**KV Cache** — Key-value tensor cache storing intermediate attention states during LLM inference. Reusing cached entries for shared prompt prefixes (Prefix Caching) avoids redundant computation and reduces latency. See [Architecture Overview](../architecture/README.md).

**KV-Cache Indexer** — A Consultant component that maintains a globally consistent view of KV-cache block distribution across Model Servers using KV-Events, enabling precise prefix cache-aware routing. See [KV-Cache Indexer](../architecture/advanced/kv-indexer.md).

**KV-Events** — Events emitted by Model Servers (via ZeroMQ) when KV-cache blocks are created or evicted. Consumed by the KV-Cache Indexer for real-time cache state tracking.

**Latency Predictor** — A Consultant that uses XGBoost quantile regression models trained on live traffic to predict per-endpoint TTFT and TPOT, enabling SLO-aware routing. See [Latency Predictor](../architecture/advanced/latency-predictor.md).

**llm-d** — A Kubernetes-native distributed inference serving stack that adds intelligent routing, KV Cache-aware routing, Disaggregated Serving, and autoscaling on top of existing Model Servers. See [Introduction](../getting-started/README.md).

**MoE (Mixture of Experts)** — A model architecture where only a subset of "expert" sub-networks activate per token, enabling very large models (e.g., DeepSeek-R1) to run efficiently. llm-d supports MoE serving via Wide Expert Parallelism.

**Model Server** — The inference engine (e.g., vLLM, SGLang) that loads model weights, runs inference on hardware accelerators, and manages a local KV Cache. The EPP routes requests to the optimal server instance. See [Model Servers](../architecture/core/model-servers.md).

**NIXL** — NVIDIA Inference Xfer Library for high-speed GPU-to-GPU KV-cache transfer over InfiniBand, RoCE, EFA, and TCP. Used between Prefill and Decode workers in Disaggregated Serving.

**Plugin Pipeline** — The modular Filter, Score, Pick architecture inside the EPP that evaluates and selects Model Server endpoints for each request. Filters narrow candidates, scorers rank them, and pickers make the final selection. See [EPP](../architecture/core/epp/README.md).

**Prefix Caching** — A technique where the EPP routes requests to Model Servers that already hold matching KV Cache entries for the prompt prefix, eliminating redundant Prefill computation and reducing TTFT. See [Architecture Overview](../architecture/README.md).

**Prefill** — The first phase of LLM inference that processes all input tokens in parallel to populate the KV Cache. Prefill latency is the dominant component of TTFT. See [Architecture Overview](../architecture/README.md).

**Proxy** — The L7 data-plane component that accepts client requests and delegates routing decisions to the EPP via ext-proc. Can be deployed via Gateway API or in Standalone mode with a GAIE-conformant proxy (such as Envoy) running as a sidecar to the EPP. See [Proxy](../architecture/core/proxy.md).

**Saturation Detector** — A safety mechanism in the EPP that evaluates whether the backend InferencePool is overloaded based on queue depth and KV-cache utilization, triggering Flow Control or request shedding.

**SGLang** — An open-source LLM serving engine that can be used as a Model Server backend in llm-d, providing RadixAttention-based Prefix Caching and disaggregation support. See [Model Servers](../architecture/core/model-servers.md).

**Tensor Parallelism (TP)** — Sharding model layers across multiple GPUs within a node to serve models that exceed single-GPU memory. See [Model Servers](../architecture/core/model-servers.md).

**Tiered KV Prefix Caching** — Extending Prefix Caching capacity beyond GPU high-bandwidth memory (HBM) by offloading KV Cache entries through a storage hierarchy: HBM, CPU memory, local SSD, and remote filesystem. See [Introduction](../getting-started/README.md).

**TPOT (Time Per Output Token)** — The average latency to generate each subsequent token during Decode. A key metric for streaming response quality. See [Architecture Overview](../architecture/README.md).

**TTFT (Time To First Token)** — The latency from request arrival to the first generated output token, dominated by Prefill time. Prefix Caching is the primary optimization for reducing TTFT. See [Architecture Overview](../architecture/README.md).

**vLLM** — An open-source high-throughput LLM serving engine and the default Model Server in llm-d. Provides PagedAttention, continuous batching, Prefix Caching, and KV-Events for cache-aware routing. See [Model Servers](../architecture/core/model-servers.md).

**Well-Lit Path** — A pre-validated, end-to-end deployment recipe (model + hardware + Helm values + benchmarks) that the llm-d community tests and supports as a first-class configuration. See [Introduction](../getting-started/README.md).

**Wide Expert Parallelism** — A deployment pattern for large MoE models that combines Data Parallelism and Expert Parallelism across multiple nodes, maximizing KV-cache space for long-context serving. See [Introduction](../getting-started/README.md).

**Workload Variant Autoscaler (WVA)** — A multi-model, SLO-aware autoscaler that optimizes cost on heterogeneous hardware by measuring instance capacity, deriving load functions, and calculating the optimal mix of model variants. See [Architecture Overview](../architecture/README.md).
