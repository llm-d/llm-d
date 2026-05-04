# KV Cache Management

Key-Value (KV) cache management is the foundation of high-performance LLM serving in llm-d. By efficiently tracking, preserving, and reusing the KV cache—the intermediate state generated during LLM inference—llm-d significantly reduces latency and increases the overall throughput of the inference pool.

The KV cache management ecosystem in llm-d consists of three core architectural pillars:

## 1. Prefix-Cache Aware Routing
The "intelligence" layer managed by the **llm-d Router** (via its **EPP** component) that determines the optimal model server Pod for each incoming request. It aims to maximize "cache hits" by routing requests to replicas that already contain the relevant KV cache for the request's prompt prefix.

- **[Deep Dive: Prefix-Cache Aware Routing](prefix-cache-aware-routing.md)**: Comparison of the Approximate (heuristic-based) and Precise (event-driven) routing implementations.

## 2. KV-Cache Indexing
The "observability" layer that maintains a real-time, globally consistent view of the cache state across all active model servers. It consumes high-frequency events from engines like vLLM to track the movement and eviction of individual token blocks.

- **[Deep Dive: KV-Cache Indexer](kv-indexer.md)**: How the indexer processes `KVEvents` and provides the source-of-truth for precise routing decisions.

## 3. KV Offloading
The "capacity" layer that extends the cache beyond the limited high-bandwidth memory (HBM) of accelerators (GPUs/TPUs). It enables model servers to "spill" or offload cache entries to CPU memory or local SSDs, effectively creating a tiered storage hierarchy for the KV cache.

- **[Deep Dive: KV Offloader](kv-offloader.md)**: Design of the multi-tier storage API and its integration with the inference engine.

---

## How They Compose

These three components work in concert to create a "virtuous cycle" of cache efficiency:

1.  **Index** (Know): The **KV-Cache Indexer** continuously monitors the state of the pool, knowing exactly what is cached and where (including offloaded tiers).
2.  **Route** (Use): The **Prefix-Cache Aware Router** uses the index to place requests on the best possible replica, turning cache entries into saved compute time.
3.  **Offload** (Grow): The **KV Offloader** ensures that valuable cache entries are not prematurely evicted due to accelerator memory pressure, keeping them available for future routing hits in larger, more cost-effective storage tiers.

By composing these layers, llm-d allows an inference pool to scale its "effective cache capacity" far beyond physical HBM limits, sustaining high hit rates even under heavy, diverse workloads.
