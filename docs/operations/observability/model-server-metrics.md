# Model Server Metrics

Reference for the Prometheus metrics the model servers export: vLLM, including its NIXL KV transfer and KV offloading metrics, and SGLang, including HiCache. To scrape them, see [Collect Metrics](./metrics.md); for ready-to-run queries, see the [PromQL Query Reference](./promql.md).

## vLLM

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `vllm:num_requests_running` | Active requests being processed | High values indicate GPU saturation; new requests will queue. Watch for sustained spikes |
| `vllm:num_requests_waiting` | Requests queued, waiting to be processed | Non-zero means pods are saturated. Primary signal for autoscaling decisions |
| `vllm:kv_cache_usage_perc` | KV cache utilization (0.0 to 1.0) | Above 0.9 means GPU memory is nearly full and requests may get preempted or rejected |
| `vllm:time_to_first_token_seconds` (histogram) | Time from request arrival to first generated token (TTFT) | Directly impacts user experience. Use `histogram_quantile()` to query percentiles |
| `vllm:inter_token_latency_seconds` (histogram) | Time between consecutive generated tokens (ITL) | Affects streaming response speed. High ITL causes choppy output. Use `histogram_quantile()` to query percentiles |
| `vllm:prefix_cache_hits_total` | Number of prefix cache hits | Compare with `prefix_cache_queries_total` to get hit rate. Low hit rate suggests the EPP is not routing effectively |
| `vllm:prefix_cache_queries_total` | Total prefix cache lookups | Divide `prefix_cache_hits_total` by this to get hit rate. A dropping ratio indicates routing or prompt pattern changes |
| `vllm:prompt_tokens_total` | Total input tokens processed | Use `rate()` to get tokens/sec per pod. Compare across pods to spot uneven load distribution |
| `vllm:generation_tokens_total` | Total output tokens generated | Use `rate()` alongside prompt tokens to get total throughput. A drop signals degraded model performance |

### vLLM NIXL KV Transfer Metrics

When vLLM uses `NixlConnector` for disaggregated serving, it exports metrics for the KV cache transfers between workers. See the [vLLM NixlConnector usage guide](https://docs.vllm.ai/en/latest/features/nixl_connector_usage/) for configuration details.

| Metric | What it measures | Why it matters |
| -------- | ------------------ | ---------------- |
| `vllm:nixl_xfer_time_seconds` (histogram) | Time from posting a transfer until the backend reports completion, including submission and data movement | High tail latency can indicate network congestion, large transfers, or backend delays |
| `vllm:nixl_post_time_seconds` (histogram) | Synchronous time spent submitting a transfer to the backend | A high value with otherwise normal transfer time points to descriptor setup or submission overhead |
| `vllm:nixl_bytes_transferred` (histogram) | Bytes moved per transfer observation | Shows transfer size and KV cache data volume |
| `vllm:nixl_num_descriptors` (histogram) | Memory descriptor count per transfer observation | High counts can indicate fragmented or large KV cache allocations |
| `vllm:nixl_num_failed_transfers` (counter) | Failed NIXL KV cache transfers | Transfer failures can prevent the consumer from using remote KV blocks |
| `vllm:nixl_num_failed_notifications` (counter) | Failed transfer completion notifications | Notification failures can prevent a peer from learning that a transfer completed |
| `vllm:nixl_num_kv_expired_reqs` (counter) | Requests whose KV blocks expired before the decoder read them; recorded on the prefill instance | Sustained increases can indicate that `kv_lease_duration` is too short for the workload or network |

> [!NOTE]
> With tensor parallelism, vLLM pools transfer observations from all TP ranks before exporting them. Histogram counts therefore represent rank-level transfer observations, not inference requests, and byte values do not represent the total size of one request across all ranks. These are aggregate Prometheus metrics and are not correlated with individual request or trace IDs.

### vLLM KV Offloading Metrics

When vLLM uses the native `OffloadingConnector` (the [tiered prefix cache guide](../../../guides/tiered-prefix-cache/README.md)), it exports metrics for KV blocks moved between the GPU and the offload tiers. The names below are available in vLLM v0.26.0 and later.

| Metric | What it measures | Why it matters |
|--------|------------------|----------------|
| `vllm:external_prefix_cache_queries_total` | Prompt tokens looked up through the KV connector | Denominator for the offload tier hit rate |
| `vllm:external_prefix_cache_hits_total` | Prompt tokens the connector reported as cached at scheduling time | Offload tier hits. Counted before the load finishes, so read it together with load bytes |
| `vllm:kv_offload_store_bytes_total` | Bytes stored from GPU to the offload tier | Eviction traffic into the tier |
| `vllm:kv_offload_load_bytes_total` | Bytes loaded from the offload tier back to GPU | Confirms offloaded blocks are being reused |
| `vllm:kv_offload_store_time_total`, `vllm:kv_offload_load_time_total` | Total store and load time in seconds | Divide bytes by time for effective transfer speed |
| `vllm:kv_offload_allocation_failure_total` | Store attempts that could not allocate offload blocks | Sustained increases mean the CPU tier has too few free or evictable blocks |
| `vllm:kv_offload_cpu_cache_usage_perc` | Fraction of the CPU tier pinned by in-flight transfers (0.0 to 1.0) | This is not occupancy. Sustained values near 1.0 mean stores may be dropped |

> [!NOTE]
> Older dashboards may use `vllm:kv_offload_total_bytes_total`, `vllm:kv_offload_total_time_total`, and `vllm:kv_offload_size` with a `transfer_type` label (`GPU_to_CPU`, `CPU_to_GPU`). vLLM still emits them for `CPUOffloadingSpec` and its subclasses, which covers both the CPU RAM and filesystem paths, but they are deprecated in favor of the series above.

## SGLang

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `sglang_num_running_reqs` | Active requests being processed | High values indicate GPU saturation; new requests will queue |
| `sglang_num_queue_reqs` | Requests queued, waiting to be processed | Non-zero means pods are saturated. Primary signal for autoscaling decisions |
| `sglang_token_usage` | KV cache token utilization (0.0 to 1.0) | Above 0.9 means GPU memory is nearly full |
| `sglang_cache_hit_rate` | Prefix cache hit rate (0.0 to 1.0) | High hit rate indicates efficient KV cache reuse |
| `sglang_time_to_first_token_seconds` (histogram) | Time from request arrival to first generated token (TTFT) | Directly impacts user experience. Use `histogram_quantile()` to query percentiles |
| `sglang_inter_token_latency_seconds` (histogram) | Time between consecutive generated tokens (ITL) | Affects streaming response speed. Use `histogram_quantile()` to query percentiles |
| `sglang_prompt_tokens_total` | Total input tokens processed | Use `rate()` to get tokens/sec per pod |
| `sglang_generation_tokens_total` | Total output tokens generated | Use `rate()` alongside prompt tokens to get total throughput |

### SGLang HiCache Metrics

When HiCache is enabled (the SGLang path in the [tiered prefix cache guide](../../../guides/tiered-prefix-cache/README.md)), SGLang exports metrics for the host (CPU) tier.

| Metric | What it measures | Why it matters |
|--------|------------------|----------------|
| `sglang_hicache_host_used_tokens` | Tokens currently held in the host KV cache | Divide by `sglang_hicache_host_total_tokens` for host tier occupancy |
| `sglang_hicache_host_total_tokens` | Host KV cache capacity in tokens | Denominator for host tier occupancy |
| `sglang_evicted_tokens_total` | Tokens evicted from GPU to host | Eviction traffic into the host tier |
| `sglang_load_back_tokens_total` | Tokens loaded from host back to GPU | Confirms host tier blocks are being reused |
| `sglang_load_back_duration_seconds` (histogram) | Time to load KV cache from host back to GPU | Slow load-back cuts into the TTFT gain from offloading |
| `sglang_cached_tokens_total` | Cached prompt tokens by `cache_source` (`device`, `host`, `storage_<backend>`, or `total` when unsplit) | Attributes prefix cache hits to each tier |
