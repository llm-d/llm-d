# SGLang in llm-d

SGLang is a high-performance LLM serving framework optimized for complex prompt structures, multi-turn conversations, and structured generation. In llm-d, SGLang operates as a first-class model server engine integrated with routing, prefix tracking, and disaggregation.

## Pod Labeling & Discovery

SGLang instances are discovered by the Endpoint Picker (EPP) using [`InferencePool`](../router/inferencepool.md) label selectors. To ensure that the EPP correctly maps Prometheus metric names to its internal scheduling model, SGLang pods must specify the `llm-d.ai/engine-type: sglang` label:

```yaml
metadata:
  labels:
    llm-d.ai/engine-type: sglang # Required to select SGLang metric parsing
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: meta-llama-3-8b-instruct
    llm-d.ai/role: decode # Used in disaggregated topologies (prefill vs. decode)
```

## Router Telemetry & Metrics

When started with `--enable-metrics`, SGLang exposes native Prometheus metrics that the EPP scrapes on port 30000 (or the configured serving port):

* `sglang_num_queue_reqs`: Number of requests queued waiting for GPU execution.
* `sglang_num_running_reqs`: Number of requests actively processing on the engine.
* `sglang_token_usage`: Fraction of total token capacity currently occupied in the KV cache (mapped to KV cache utilization).
* `sglang_cache_config_info`: Metric exposing the `page_size` and `num_pages` labels required by prefix-cache scorers.

For full metric specifications and scraping configurations, see [Model Server Metrics](../../operations/observability/model-server-metrics.md#sglang).

## RadixAttention & KV-Cache Events

SGLang organizes KV cache memory into a Radix Tree (RadixAttention), enabling automatic cache reuse across arbitrary shared prefixes, system prompts, few-shot examples, and chat histories.

To extend prefix-aware routing across a multi-replica cluster, SGLang emits tree eviction and insertion events over ZeroMQ using `--enable-kv-cache-events` and `--kv-events-config`. The [KV-Cache Indexer](../kv-management/kv-indexer.md) ingests these events to track radix-tree states globally, empowering the EPP to perform [Precise Prefix-Cache Aware Routing](../../../guides/precise-prefix-cache-routing/README.md).

## Hierarchical KV Caching (HiCache)

SGLang features Hierarchical KV Caching (HiCache) to offload cached tokens from GPU High Bandwidth Memory (HBM) to host RAM and NVMe storage:

* `--enable-hierarchical-cache`: Activates the multi-tier storage subsystem.
* `--hicache-ratio` / `--hicache-size`: Allocates memory capacity across host and GPU tiers.
* `--hicache-write-policy`: Governs synchronous vs. asynchronous writeback to offload storage.
* `--hicache-storage-backend`: Selects the host memory or persistent filesystem backend.

For architectural design and configuration recipes, see [KV Offloading](../kv-management/kv-offloader.md) and the [Tiered Prefix Cache Guide](../../../guides/tiered-prefix-cache/README.md).

## Prefill/Decode Disaggregation

SGLang supports native disaggregation separating prefill nodes from decode nodes:

* **Engine Configuration:** Configured using `--disaggregation-mode prefill` or `--disaggregation-mode decode`.
* **Transfer Backends:** Supports low-latency KV transmission via `--disaggregation-transfer-backend nixl` (RDMA) or `--disaggregation-transfer-backend mooncake`.
* **Bootstrap Coordination:** Decode and prefill nodes synchronize metadata via an embedded prefill bootstrap server (`--disaggregation-bootstrap-port 8998`). The Routing Proxy Sidecar coordinates request sessions using `bootstrap_room` identifiers when started with `--kv-connector=sglang`.
* **Routing Decider:** Integrates with llm-d's `always-disagg-pd-decider` in the EPP for unconditional disaggregated dispatch.

For detailed operational guides, see [Disaggregated Serving](../disaggregation/pd-disaggregation.md) and [SGLang Disaggregation Operations](../../operations/disaggregation/sglang.md).

## Wide Expert Parallelism (MoE)

For massive Mixture-of-Experts (MoE) models, SGLang supports Wide Expert Parallelism across multi-node GPU clusters:

* **Expert Parallelism:** Enabled via `--ep-size <N>` and `--enable-ep-moe` using DeepEP optimized communication kernels.
* **Data-Parallel Attention:** Combines expert-parallel MLP layers with data-parallel attention via `--dp-size <M>` and `--enable-dp-attention`.

See [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md) for architectural trade-offs and deployment strategies.
