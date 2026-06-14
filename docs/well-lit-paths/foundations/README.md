# Core Capability Building Blocks

Core Capability Building Blocks represent the individual functional optimization, intelligent routing, and physical inference execution features of llm-d.

These guides teach single architectural capabilities that you can configure independently or compose together into comprehensive production workloads.

### Intelligent Routing

- **[Optimized Baseline](optimized-baseline.md)**: Strategies for handling the unique challenges of LLM request scheduling, moving beyond traditional round-robin approaches.
- **[Predicted Latency-Based Routing](predicted-latency.md)**: Using online-trained machine learning models to predict latency and optimize scheduling.
- **[Multi-Model Routing](multi-model-routing.md)**: Serving multiple LLMs and LoRA adapters behind a single Gateway endpoint using the Inference Payload Processor (IPP).

### Advanced KV-Cache Management

- **[Precise Prefix Cache Routing](precise-prefix-cache-routing.md)**: Near-real-time routing based on exact cache state published by model servers.
- **[Tiered Prefix Cache](tiered-prefix-cache.md)**: Efficiently managing KV caches by offloading to CPU RAM, NVMe, or network storage to improve prefix-cache re-use.

### Serving Large Models

- **[Prefill/Decode Disaggregation](pd-disaggregation.md)**: Separating prefill (compute-bound) and decode (memory-bandwidth-bound) phases for optimized performance.
- **[Wide Expert-Parallelism](wide-expert-parallelism.md)**: Scaling KV cache space for massive MoE models like DeepSeek-R1 using DP/EP deployment patterns.

### Traffic Control & Autoscaling

- **[Flow Control](flow-control.md)**: Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
- **[Workload Autoscaling](workload-autoscaling.md)**: From simple Kubernetes autoscaling supplemented by EPP load metrics to advanced, SLO-aware capacity optimization for heterogeneous pools via the Workload Variant Autoscaler.
