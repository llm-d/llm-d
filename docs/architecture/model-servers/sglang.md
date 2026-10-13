# SGLang in llm-d

SGLang is a high-performance LLM serving engine featuring RadixAttention prefix caching, hierarchical KV caching (HiCache), and native prefill/decode disaggregation. In llm-d, SGLang operates as a first-class model server backend integrated with the router, KV-cache indexer, and disaggregated topologies.

## Pod Labeling & Discovery

SGLang pods join an [`InferencePool`](../router/inferencepool.md) via Kubernetes label selectors. Because the EPP defaults to vLLM metric names when no engine label is present, SGLang pods **must** carry the `llm-d.ai/engine-type: sglang` label:

```yaml
metadata:
  labels:
    llm-d.ai/engine-type: sglang # Required so the EPP maps SGLang metric names
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: Qwen-Qwen3-32B
    llm-d.ai/role: decode # prefill, decode, or prefill-decode
```

## EPP <-> SGLang Protocol & Metrics Reporting

When started with `--enable-metrics`, SGLang exposes Prometheus metrics (`GET /metrics` on the serving port) that the [EPP](../router/epp.md) scrapes for scheduling and saturation control:

| EPP Signal | Type | Description | SGLang Prometheus Metric |
| --- | --- | --- | --- |
| `TotalQueuedRequests` | Gauge | Current number of requests waiting in the queue | `sglang_num_queue_reqs` |
| `TotalRunningRequests` | Gauge | Current number of requests actively executing on the engine | `sglang_num_running_reqs` |
| `KVCacheUtilization` | Gauge | Current KV-cache token occupancy (`0.0` to `1.0`) | `sglang_token_usage` |
| `BlockSize` *(optional)* | Gauge (labeled) | Page size in tokens used by the radix cache; falls back to the [prefix plugin config](https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/prefix-aware/#customize-the-prefix-cache-plugin) if absent | `sglang_cache_config_info` (`page_size` label) |
| `NumGPUBlocks` *(optional)* | Gauge (labeled) | Total number of pages in the GPU KV cache; falls back to the prefix plugin config if absent | `sglang_cache_config_info` (`num_pages` label) |

For the full list of SGLang latency, token, and HiCache metrics, see [Model Server Metrics](../../operations/observability/model-server-metrics.md#sglang).

## RadixAttention & KV-Cache Events

SGLang manages KV-cache memory in a radix tree (`RadixAttention`), automatically reusing cached KV pages across shared system prompts, few-shot examples, and multi-turn chat histories.

To enable cluster-wide prefix-cache affinity, SGLang publishes radix-tree insertion and eviction events over ZeroMQ when started with `--enable-kv-cache-events` and `--kv-events-config`. The [KV-Cache Indexer](../kv-management/kv-indexer.md) consumes these events so the EPP can perform [Precise Prefix-Cache Aware Routing](../../../guides/precise-prefix-cache-routing/README.md).

## Hierarchical KV Caching (HiCache)

SGLang includes Hierarchical KV Caching (HiCache) to extend KV-cache capacity from GPU HBM into host CPU memory and external storage backends:

- `--enable-hierarchical-cache`: Enables the host memory tier alongside the GPU radix cache.
- `--hicache-ratio` / `--hicache-size`: Controls host KV-cache sizing relative to GPU capacity or as a fixed size.
- `--hicache-write-policy`: Selects write-through or write-back caching behavior between GPU and host tiers.
- `--hicache-storage-backend`: Connects an additional storage tier (such as filesystem or Mooncake).

See [KV Offloading](../kv-management/kv-offloader.md) and the [Tiered Prefix Cache guide](../../../guides/tiered-prefix-cache/README.md) for architecture and deployment details.

## Prefill/Decode Disaggregation

SGLang supports disaggregated prefill and decode pools coordinated through a prefill bootstrap server and the llm-d Routing Proxy Sidecar:

- **Engine Mode & Transport:** Started with `--disaggregation-mode prefill` or `--disaggregation-mode decode` and `--disaggregation-transfer-backend nixl` (or `mooncake`), where the prefill worker pushes KV blocks to the decode worker over RDMA.
- **Bootstrap Server Rendezvous:** Each prefill instance runs a bootstrap server (`--disaggregation-bootstrap-port 8998`). For each request, the Routing Proxy Sidecar (`--kv-connector=sglang`) generates a `bootstrap_room` ID and injects `bootstrap_host`, `bootstrap_port`, and `bootstrap_room` into the prefill and decode requests.
- **Unconditional Disaggregation:** An SGLang decode worker (`--disaggregation-mode=decode`) requires every request to include bootstrap rendezvous fields and cannot fall back to local prefill. Deployments with SGLang therefore use `always-disagg-pd-decider` in the EPP.

See [Disaggregated Serving](../disaggregation/pd-disaggregation.md) and [SGLang Disaggregation Operations](../../operations/disaggregation/sglang.md) for details.

## Wide Expert Parallelism (MoE)

For large Mixture-of-Experts (MoE) models, SGLang combines data-parallel attention (`--dp-size`, `--enable-dp-attention`) with expert-parallel MoE layers (`--ep-size`, `--enable-ep-moe`) using DeepEP all-to-all kernels across multi-node [LeaderWorkerSet](../model-server-orchestration/leaderworkerset.md) or [DisaggregatedSet](../model-server-orchestration/disaggregatedset.md) groups. See [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md).

## Health Checks

SGLang exposes HTTP health endpoints used for Kubernetes liveness, readiness, and startup probes:

- **Liveness:** `GET /health` — confirms the SGLang server process is alive.
- **Readiness:** `GET /health` (or `GET /health_generate`) — confirms the model is loaded and ready to accept inference requests. See [Readiness Probes](../../operations/lifecycle/readiness-probes.md).

## Further Reading

- [SGLang Documentation](https://docs.sglang.io/)
- [InferencePool](../router/inferencepool.md) — how model server pods are discovered and grouped
- [Endpoint Picker (EPP)](../router/epp.md) — how the router schedules requests using SGLang telemetry
