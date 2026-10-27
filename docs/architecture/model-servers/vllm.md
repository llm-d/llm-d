# vLLM in llm-d

vLLM is the default model server engine in llm-d, delivering high-throughput LLM inference via PagedAttention, continuous batching, and native integrations across llm-d's routing, caching, and disaggregation layers.

## Pod Labeling & Discovery

Model server pods running vLLM join an [`InferencePool`](../router/inferencepool.md) through standard Kubernetes label selectors. Pods without an `llm-d.ai/engine-type` label default to vLLM metric names:

```yaml
metadata:
  labels:
    llm-d.ai/engine-type: vllm # Default engine type if omitted
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: Qwen-Qwen3-32B
    llm-d.ai/role: decode # prefill, decode, or prefill-decode
```

## EPP <-> vLLM Protocol & Metrics Reporting

By default, the [EPP](../router/epp.md) scrapes metrics from vLLM (`GET /metrics` on the serving port, default `8000`) to make request scheduling and flow-control decisions:

| EPP Signal | Type | Description | vLLM Prometheus Metric |
| --- | --- | --- | --- |
| `TotalQueuedRequests` | Gauge | Current number of requests waiting in the engine queue | `vllm:num_requests_waiting` |
| `TotalRunningRequests` | Gauge | Current number of requests actively executing on the engine | `vllm:num_requests_running` |
| `KVCacheUtilization` | Gauge | Current GPU KV-cache utilization (`0.0` to `1.0`) | `vllm:kv_cache_usage_perc` |
| `BlockSize` *(optional)* | Gauge (labeled) | Token block size used to allocate KV memory; falls back to the [prefix plugin config](https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/prefix-aware/#customize-the-prefix-cache-plugin) if absent | `vllm:cache_config_info` (`block_size` label) |
| `NumGPUBlocks` *(optional)* | Gauge (labeled) | Total number of blocks in the HBM KV cache; falls back to the prefix plugin config if absent | `vllm:cache_config_info` (`num_gpu_blocks` label) |

For the full list of vLLM latency, token, NIXL transfer, and KV offloading metrics, see [Model Server Metrics](../../operations/observability/model-server-metrics.md#vllm).

## Dynamic LoRA Adapter Serving

vLLM supports dynamically loading and offloading Parameter-Efficient Fine-Tuning (PEFT) LoRA adapters at runtime when a request specifies an adapter name in the OpenAI `model` field.

To enable LoRA-affinity scheduling in the EPP, vLLM exposes the `vllm:lora_requests_info` gauge on `/metrics`:

- **Metric name:** `vllm:lora_requests_info`
- **Metric type:** Gauge
- **Metric value:** Last-updated timestamp (used by the EPP to identify the most recent state)
- **Metric labels:**
  - `max_lora`: Maximum number of adapters that can be loaded in GPU memory concurrently (e.g., `"8"`). Requests are queued when the server has reached `max_lora` and needs to swap an adapter.
  - `running_lora_adapters`: Comma-separated list of adapters currently loaded in GPU memory and ready to serve requests (e.g., `"adapter1, adapter2"`).
  - `waiting_lora_adapters`: Comma-separated list of adapters waiting to be loaded and served (e.g., `"adapter1, adapter2"`).

## Prefix Caching & KV-Cache Events

vLLM provides [Automatic Prefix Caching (APC)](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html) (`--enable-prefix-caching`), allowing requests that share a prompt prefix to reuse KV blocks already in GPU memory.

For cluster-wide cache-aware routing, vLLM can publish real-time block allocation and eviction events over ZeroMQ via `--kv-events-config` (`ZmqEventPublisher`). The [KV-Cache Indexer](../kv-management/kv-indexer.md) subscribes to these ZMQ streams to maintain a global view of cached prefixes across pods, powering [Precise Prefix-Cache Aware Routing](../../../guides/precise-prefix-cache-routing/README.md).

## Tiered KV Offloading & P2P Sharing

To extend effective KV capacity beyond GPU HBM, vLLM integrates with multi-tier and peer-to-peer KV connectors via `--kv-transfer-config`:

- **Native `OffloadingConnector`:** Offloads evicted KV blocks to host CPU memory (`CPUOffloadingSpec`) or a shared filesystem.
- **`LMCacheConnector` & `MooncakeConnector`:** Connects vLLM to LMCache or Mooncake for host/remote KV pooling and cross-pod P2P KV transfer.

See [KV Offloading](../kv-management/kv-offloader.md) and [P2P KV-Cache Sharing](../kv-management/p2p-kv-cache-sharing.md) for architecture details.

## Prefill/Decode Disaggregation

In disaggregated serving, compute-bound prefill and memory-bandwidth-bound decode run on separate vLLM pod pools:

- **KV Transport:** Uses `NixlConnector` over RDMA (InfiniBand, RoCE, EFA) so decode workers pull KV cache blocks directly from prefill GPU memory.
- **Dynamic Handshake:** Decode and prefill workers establish peer-to-peer connections lazily on first pairing via a background ZMQ metadata exchange (`NIXLMetadata`), without a central coordinator.
- **Conditional Disaggregation & Fallback:** When `do_remote_prefill` is absent from a request, a vLLM decode worker runs prefill locally, allowing the EPP to skip disaggregation for short or already-cached prompts. Setting `kv_load_failure_policy=recompute` allows decode pods to recompute prefill locally if a remote KV pull fails during scale-down.

See [Disaggregated Serving](../disaggregation/pd-disaggregation.md) and [vLLM Disaggregation Operations](../../operations/disaggregation/vllm.md) for details.

## Wide Expert Parallelism (MoE)

For large Mixture-of-Experts (MoE) models, vLLM combines data-parallel attention (`--data-parallel-size`) with expert-parallel MLP layers (`--enable-expert-parallel`) across multi-node [LeaderWorkerSet](../model-server-orchestration/leaderworkerset.md) or [DisaggregatedSet](../model-server-orchestration/disaggregatedset.md) groups, using DeepEP or PPLX kernels for cross-rank token dispatch and combine. See [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md).

## Health Checks

vLLM exposes HTTP health endpoints used for Kubernetes liveness, readiness, and startup probes:

- **Liveness:** `GET /health` — confirms the vLLM server process is alive.
- **Readiness:** `GET /health` — returns `200 OK` once model weights are loaded and the engine is ready to serve requests. See [Readiness Probes](../../operations/lifecycle/readiness-probes.md).

## Further Reading

- [vLLM Documentation](https://docs.vllm.ai/)
- [InferencePool](../router/inferencepool.md) — how model server pods are discovered and grouped
- [Endpoint Picker (EPP)](../router/epp.md) — how the router schedules requests using vLLM telemetry
