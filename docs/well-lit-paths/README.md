# Well-Lit Paths

Well-lit paths are curated, end-to-end guides for common LLM inference patterns, platform operations, and workload-specific optimizations.

These guides are organized into three foundational architectural groupings:

### 1. [Core Capability Building Blocks](capabilities/README.md)
Individual functional features, sophisticated routing algorithms, and physical inference execution paths.
- **Intelligent Routing**: [Optimized Baseline](capabilities/optimized-baseline.md), [Predicted Latency-Based Routing](capabilities/predicted-latency.md)
- **Advanced KV-Cache Management**: [Precise Prefix Cache Routing](capabilities/precise-prefix-cache-routing.md), [Tiered Prefix Cache](capabilities/tiered-prefix-cache.md)
- **Serving Large Models**: [Prefill/Decode Disaggregation](capabilities/pd-disaggregation.md), [Wide Expert-Parallelism](capabilities/wide-expert-parallelism.md)

### 2. [Workloads](workloads/README.md)
Cohesive, production-grade deployment guides that compose our capability building blocks into one horizontal stack tuned for a use case.
- **[Agentic Serving](workloads/agentic-serving.md)**, **[Multimodal Serving](workloads/multimodal-serving.md)**, **[Batch Serving](workloads/batch-serving/README.md)**

### 3. [Operational Excellence](operations/README.md)
Platform governance traits, capacity optimization, traffic control, and non-Kubernetes environmental adaptations.
- **[Flow Control](operations/flow-control.md)**, **[Workload Autoscaling](operations/workload-autoscaling.md)**, **[No-Kubernetes Deployment](operations/no-kubernetes-deployment.md)**
