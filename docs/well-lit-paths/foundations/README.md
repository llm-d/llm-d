# Core Capability Building Blocks

Core Capability Building Blocks represent the individual functional optimization, intelligent routing, and physical inference execution features of llm-d.

These guides teach single architectural capabilities that you can configure independently or compose together into comprehensive production workloads.

### Intelligent Routing

- **[Optimized Baseline](../../../guides/optimized-baseline/README.md)**: Strategies for handling the unique challenges of LLM request scheduling, moving beyond traditional round-robin approaches.
- **[Predicted Latency-Based Routing](../../../guides/predicted-latency-routing/README.md)**: Using online-trained machine learning models to predict latency and optimize scheduling.

### Advanced KV-Cache Management

- **[Precise Prefix Cache Routing](../../../guides/precise-prefix-cache-routing/README.md)**: Near-real-time routing based on exact cache state published by model servers.
- **[Tiered Prefix Cache](../../../guides/tiered-prefix-cache/README.md)**: Efficiently managing KV caches by offloading to CPU RAM, NVMe, or network storage to improve prefix-cache re-use.
- **[Enable P2P Prefix Cache Sharing](../../../guides/p2p-kv-cache-sharing/README.md)**: Pulling cached prefix KV blocks directly from a peer's CPU offload tier instead of recomputing them, turning per-pod prefix caches into a fleet-wide resource.

### Serving Large Models

- **[Prefill/Decode Disaggregation](../../../guides/pd-disaggregation/README.md)**: Separating prefill (compute-bound) and decode (memory-bandwidth-bound) phases for optimized performance.
- **[Wide Expert-Parallelism](../../../guides/wide-ep/README.md)**: Scaling KV cache space for massive MoE models like DeepSeek-R1 using DP/EP deployment patterns.

### Multimodal and Omni Models

- **[Serve Multimodal Models](../../../guides/multimodal-serving/README.md)**: Routing image, video, and audio requests on prefix-cache affinity that covers the media as well as the text, with aggregated serving or dedicated Encode workers (E/PD, E/P/D).
- **[Serve Omni Models](../../../guides/omni-serving/README.md)**: Serving a model that answers in text and audio (or generates images) from one vLLM-Omni pool behind the llm-d Router.
