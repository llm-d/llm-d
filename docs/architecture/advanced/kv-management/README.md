# KV Cache Management

Key-Value (KV) cache management is the foundation of high-performance LLM serving in llm-d. By efficiently tracking, preserving, and reusing the KV cache—the intermediate state generated during LLM inference—llm-d significantly reduces latency and increases the overall throughput of the inference pool. 

The KV cache management ecosystem in llm-d consists of three core architectural pillars. By composing these layers, llm-d allows an inference pool to scale its "effective cache capacity" far beyond physical HBM limits, sustaining high hit rates even under heavy, diverse workloads. The three pillars are:

## Prefix-Cache Aware Routing
The "intelligence" layer managed by the **llm-d Router** (via its **EPP** component) that uses the index to determine the optimal model server Pod for each incoming request. It aims to maximize "cache hits" by routing requests to replicas that already contain the relevant KV cache for the request's prompt prefix.

See **[Prefix-Cache Aware Routing](prefix-cache-aware-routing.md)** for a deep dive into the two different routing implementations that the llm-d Router offers: the Approximate (heuristic-based) and the Precise (event-driven) implementations.

## KV-Cache Indexing
The "observability" layer that continuously monitors the state of the pool, knowing exactly what is cached and where (including offloaded tiers and across all active model servers). It consumes high-frequency events from engines like vLLM to track the movement and eviction of individual token blocks.

See **[KV-Cache Indexer](kv-indexer.md)** for a deep dive into how the indexer processes `KVEvents` and provides the source-of-truth for precise routing decisions.

## KV Offloading
The "capacity" layer that extends the cache beyond the limited high-bandwidth memory (HBM) of accelerators (GPUs/TPUs). It enables model servers to "spill" or offload cache entries to CPU memory or local SSDs, effectively creating a tiered storage hierarchy for the KV cache.

**See [KV Offloader](kv-offloader.md)** for a deep dive into the design of the multi-tier storage API and its integration with the inference engine.



