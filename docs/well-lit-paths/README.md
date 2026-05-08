# Well-Lit Paths

Well-lit paths are curated, end-to-end guides for common LLM inference patterns and optimizations. These guides are intended to be a starting point for your own configuration and deployment of model servers. Our manifests provide basic reusable building blocks for vLLM deployments and llm-d router configuration within these guides but are not intended to support the full range of all possible configurations.

### Intelligent Routing

- **[Optimized Baseline](optimized-baseline.md)**: Strategies for handling the unique challenges of LLM request scheduling, moving beyond traditional round-robin approaches.
- **[Predicted Latency-Based Routing](predicted-latency.md)**: Using online-trained machine learning models to predict latency and optimize scheduling.

### Advanced KV-Cache Management

- **[Precise Prefix Cache Routing](precise-prefix-cache-aware.md)**: Near-real-time routing based on exact cache state published by model servers.
- **[Tiered Prefix Cache](tiered-prefix-cache.md)**: Efficiently managing KV caches by offloading to CPU RAM, NVMe, or network storage to improve prefix-cache re-use.

### Serving Large Models

- **[Prefill/Decode Disaggregation](pd-disaggregation.md)**: Separating prefill (compute-bound) and decode (memory-bandwidth-bound) phases for optimized performance.
- **[Wide Expert-Parallelism](wide-expert-parallelism.md)**: Scaling KV cache space for massive MoE models like DeepSeek-R1 using DP/EP deployment patterns.

### Operational Excellence

- **[Flow Control](flow-control.md)**: Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
- **[Workload Autoscaling](workload-autoscaling.md)**: From simple Kubernetes autoscaling supplemented by EPP load metrics to advanced, SLO-aware capacity optimization for heterogeneous pools via the Workload Variant Autoscaler.

### Experimental

- **[Asynchronous Processing](asynchronous-processing.md)**: Intelligently processing latency-tolerant requests sourced from message queues via a lightweight agent to leverage "slack" capacity without the complexity of a full batch gateway.
- **[Batch Gateway](experimental/batch-gateway.md)**: Managing large-scale batch inference coexisting with interactive workloads via an OpenAI-compatible Batch API.

