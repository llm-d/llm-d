# Well-Lit Paths

Well-lit paths are curated, end-to-end guides for common LLM inference patterns and optimizations. These guides are intended to be a starting point for your own configuration and deployment of model servers. Our manifests provide basic reusable building blocks for vLLM deployments and llm-d router configuration within these guides but are not intended to support the full range of all possible configurations.

> **Serving a specific workload?** See **[Workloads](#workloads)** — guides (e.g. agentic and multimodal serving) that compose these paths into a cohesive deployment for a use case.

### Workloads

Workload guides provide the recommended, cohesive deployment for serving a production workload on llm-d. Each defines the workload, then composes the relevant well-lit paths into one stack tuned to serve it.

- **[Agentic Serving](workloads/agentic-serving.md)**: Long, multi-turn, tool-using agentic programs (e.g. coding agents) — prefix-aware routing, KV-cache offloading, and P/D disaggregation composed for the agentic workload.
- **[Multimodal Serving](workloads/multimodal-serving.md)**: Image / audio / video workloads — prefix- and load-aware routing that tracks and matches multimodal payloads across aggregated and disaggregated serving.
- **[Batch Serving](workloads/batch-serving/README.md)**: Large-scale offline or asynchronous jobs — OpenAI-compatible batch gateway and lightweight async queue processors.

### Intelligent Routing

- **[Optimized Baseline](optimized-baseline.md)**: Strategies for handling the unique challenges of LLM request scheduling, moving beyond traditional round-robin approaches.
- **[Predicted Latency-Based Routing](predicted-latency.md)**: Using online-trained machine learning models to predict latency and optimize scheduling.

### Advanced KV-Cache Management

- **[Precise Prefix Cache Routing](precise-prefix-cache-routing.md)**: Near-real-time routing based on exact cache state published by model servers.
- **[Tiered Prefix Cache](tiered-prefix-cache.md)**: Efficiently managing KV caches by offloading to CPU RAM, NVMe, or network storage to improve prefix-cache re-use.

### Serving Large Models

- **[Prefill/Decode Disaggregation](pd-disaggregation.md)**: Separating prefill (compute-bound) and decode (memory-bandwidth-bound) phases for optimized performance.
- **[Wide Expert-Parallelism](wide-expert-parallelism.md)**: Scaling KV cache space for massive MoE models like DeepSeek-R1 using DP/EP deployment patterns.

### Operational Excellence

- **[Flow Control](flow-control.md)**: Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
- **[Workload Autoscaling](workload-autoscaling.md)**: From simple Kubernetes autoscaling supplemented by EPP load metrics to advanced, SLO-aware capacity optimization for heterogeneous pools via the Workload Variant Autoscaler.

### Experimental

- **[No-Kubernetes Deployment](no-kubernetes-deployment.md)**: Running the llm-d routing stack on bare metal, HPC schedulers, or Ray — workers are discovered from a YAML file on disk via the `file-discovery` plugin instead of an `InferencePool`.
