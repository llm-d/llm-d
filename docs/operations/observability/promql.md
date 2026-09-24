# PromQL Query Reference

Ready-to-use PromQL queries for monitoring llm-d deployments. Use these in the Prometheus UI or as the basis for Grafana panels. For a default set of ready-to-apply alerts built on these metrics, see [Alerting](./alerting.md).

To generate traffic and populate error metrics for testing, use the [traffic generation script](../../../guides/recipes/observability/generate-traffic-basic.sh).

## Tier 1: Immediate Failure & Saturation Indicators

Start here when something looks wrong.

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Overall Error Rate** | `sum(rate(llm_d_epp_request_error_total[5m])) / sum(rate(llm_d_epp_request_total[5m]))` |
| **Per-Model Error Rate** | `sum by(model_name) (rate(llm_d_epp_request_error_total[5m])) / sum by(model_name) (rate(llm_d_epp_request_total[5m]))` |
| **Request Preemptions** | `sum by(pod, instance) (rate(vllm:num_preemptions[5m]))` |
| **Overall Latency P90** | `histogram_quantile(0.90, sum by(le) (rate(llm_d_epp_request_duration_seconds_bucket[5m])))` |
| **Overall Latency P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_request_duration_seconds_bucket[5m])))` |
| **TTFT P99 per model** | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))` |
| **TTFT P99 per model (SGLang)** | `histogram_quantile(0.99, sum by(le, model_name) (rate(sglang_time_to_first_token_seconds_bucket[5m])))` |
| **Inter-Token Latency P99** | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:inter_token_latency_seconds_bucket[5m])))` |
| **Inter-Token Latency P99 (SGLang)** | `histogram_quantile(0.99, sum by(le, model_name) (rate(sglang_inter_token_latency_seconds_bucket[5m])))` |
| **Request Rate** | `sum by(model_name) (rate(llm_d_epp_request_total[5m]))` |
| **GPU Utilization** | `avg by(gpu, node) (DCGM_FI_DEV_GPU_UTIL or nvidia_gpu_duty_cycle)` |
| **EPP E2E Latency P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_scheduler_e2e_duration_seconds_bucket[5m])))` |
| **EPP Plugin Latency P99** | `histogram_quantile(0.99, sum by(le, plugin_type) (rate(llm_d_epp_plugin_duration_seconds_bucket[5m])))` |

## Tier 2: Diagnostic Drill-Down

### GKE TPU Hardware

For GKE TPU hardware metrics, see the [TPU metric interface and checks](./tpu.md#metric-interface).
For example, `tensorcore_utilization{job="kube-system/tpu-metrics-exporter",make="cloud-tpu"}`
returns per-series utilization in percent; `memory_used{job="kube-system/tpu-metrics-exporter",make="cloud-tpu"}`
returns bytes. Substitute the actual scrape job. These gauges do not need `rate()`.
Keep accelerator and instance labels when displaying them, and do not substitute
zero for an absent series. The [TPU dashboard](../../../guides/recipes/observability/grafana/dashboards/llm-d-tpu-overview.json)
adds instance, model, and topology filters.

### Basic Model Serving

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **KV Cache Utilization** | `avg by(pod, model_name) (vllm:kv_cache_usage_perc)` |
| **KV Cache Utilization (SGLang)** | `avg by(pod, model_name) (sglang_token_usage)` |
| **Request Queue Depth** | `sum by(pod, model_name) (vllm:num_requests_waiting)` |
| **Request Queue Depth (SGLang)** | `sum by(pod, model_name) (sglang_num_queue_reqs)` |
| **Active Requests** | `avg by(pod) (vllm:num_requests_running)` |
| **Active Requests (SGLang)** | `avg by(pod) (sglang_num_running_reqs)` |
| **Total Throughput** (tokens/sec) | `sum by(model_name, pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Total Throughput (SGLang)** (tokens/sec) | `sum by(model_name, pod) (rate(sglang_prompt_tokens_total[5m]) + rate(sglang_generation_tokens_total[5m]))` |
| **Generation Token Rate** | `sum by(model_name, pod) (rate(vllm:generation_tokens_total[5m]))` |
| **Generation Token Rate (SGLang)** | `sum by(model_name, pod) (rate(sglang_generation_tokens_total[5m]))` |

### Routing & Load Balancing

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **QPS per endpoint** | `sum by(endpoint_name) (rate(llm_d_epp_scheduler_attempts_total{status="success"}[5m]))` |
| **Token distribution per pod** | `sum by(pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Token distribution per pod (SGLang)** | `sum by(pod) (rate(sglang_prompt_tokens_total[5m]) + rate(sglang_generation_tokens_total[5m]))` |
| **Routing decision latency P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_plugin_duration_seconds_bucket[5m])))` |

### Prefix Caching

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Cache hit rate** | `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))` |
| **Cache hit rate (SGLang)** | `avg(sglang_cache_hit_rate)` |
| **Per-pod hit rate** | `sum by(pod) (rate(vllm:prefix_cache_hits_total[5m])) / sum by(pod) (rate(vllm:prefix_cache_queries_total[5m]))` |
| **Per-pod hit rate (SGLang)** | `sglang_cache_hit_rate` |
| **EPP prefix indexer size** | `llm_d_epp_prefix_indexer_size` |
| **EPP prefix hit ratio P90** | `histogram_quantile(0.90, sum by(le) (rate(llm_d_epp_prefix_indexer_hit_ratio_bucket[5m])))` |

### Tiered Prefix Cache

Queries for the [tiered prefix cache guide](../../../guides/tiered-prefix-cache/README.md). The `vllm:kv_offload_*` series come from vLLM's native `OffloadingConnector`.

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Offload tier hit rate** | `sum(rate(vllm:external_prefix_cache_hits_total[5m])) / sum(rate(vllm:external_prefix_cache_queries_total[5m]))` |
| **Offload store rate per pod (MiB/sec)** | `sum by(pod) (rate(vllm:kv_offload_store_bytes_total[5m])) / 1048576` |
| **Offload load rate per pod (MiB/sec)** | `sum by(pod) (rate(vllm:kv_offload_load_bytes_total[5m])) / 1048576` |
| **Offload load speed per pod (MiB per second of load time)** | `sum by(pod) (rate(vllm:kv_offload_load_bytes_total[5m])) / sum by(pod) (rate(vllm:kv_offload_load_time_total[5m])) / 1048576` |
| **Offload store allocation failures in 5m** | `sum by(pod) (increase(vllm:kv_offload_allocation_failure_total[5m]))` |
| **EPP prefix index size by tier** | `sum by(plugin_name) (llm_d_epp_prefix_indexer_size)` |
| **EPP prefix hit ratio P90 by tier** | `histogram_quantile(0.90, sum by(le, plugin_name) (rate(llm_d_epp_prefix_indexer_hit_ratio_bucket[5m])))` |
| **Host tier usage (SGLang HiCache)** | `sum by(pod) (sglang_hicache_host_used_tokens) / sum by(pod) (sglang_hicache_host_total_tokens)` |
| **Share of prompt tokens served from host tier (SGLang HiCache)** | `sum(rate(sglang_cached_tokens_total{cache_source="host"}[5m])) / sum(rate(sglang_prompt_tokens_total[5m]))` |
| **Retrieve hit rate (LMCache)** | `avg by(pod) (lmcache:retrieve_hit_rate)` |

`vllm:external_prefix_cache_hits_total` counts tokens the connector reports as available at scheduling time, before the load completes. `vllm:kv_offload_load_bytes_total` counts bytes actually loaded back to the GPU.

### Prefill/Decode Disaggregation

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Prefill worker utilization** | `avg by(pod) (vllm:num_requests_running{pod=~".*prefill.*"})` |
| **Prefill worker utilization (SGLang)** | `avg by(pod) (sglang_num_running_reqs{pod=~".*prefill.*"})` |
| **Decode KV cache utilization** | `avg by(pod) (vllm:kv_cache_usage_perc{pod=~".*decode.*"})` |
| **Disaggregation decision ratio** | `sum(rate(llm_d_epp_disagg_decision_total{decision_type="prefill-decode"}[5m])) / sum(rate(llm_d_epp_disagg_decision_total[5m]))` |
| **Average NIXL transfer time (ms)** | `sum(rate(vllm:nixl_xfer_time_seconds_sum[5m])) / sum(rate(vllm:nixl_xfer_time_seconds_count[5m])) * 1000` |
| **NIXL transfer time P95 (ms)** | `histogram_quantile(0.95, sum by(le) (rate(vllm:nixl_xfer_time_seconds_bucket[5m]))) * 1000` |
| **Average NIXL transfer size (MiB)** | `sum(rate(vllm:nixl_bytes_transferred_sum[5m])) / sum(rate(vllm:nixl_bytes_transferred_count[5m])) / 1048576` |
| **NIXL transfer data volume (MiB/sec)** | `sum(rate(vllm:nixl_bytes_transferred_sum[5m])) / 1048576` |
| **Failed NIXL transfers in 5m** | `sum(increase(vllm:nixl_num_failed_transfers[5m]))` |
| **Failed NIXL notifications in 5m** | `sum(increase(vllm:nixl_num_failed_notifications[5m]))` |
| **Expired prefill KV requests in 5m** | `sum(increase(vllm:nixl_num_kv_expired_reqs[5m]))` |

NIXL histogram observations are pooled across tensor-parallel ranks. Their counts and average sizes describe rank-level transfer observations, not inference requests or whole-request KV cache sizes.

### Wide Expert Parallelism

Queries for the [wide expert parallelism guide](../../../guides/wide-ep/README.md). That guide's monitoring overlay scrapes every DP rank as its own target, so `endpoint` carries the rank port name (`rank0` to `rank7`) and `job` carries `<namespace>/prefill` or `<namespace>/decode`.

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Active requests per DP rank** | `sum by(pod, endpoint) (vllm:num_requests_running)` |
| **KV cache utilization per DP rank** | `avg by(pod, endpoint) (vllm:kv_cache_usage_perc)` |
| **Rank spread within a role** | `max by(job) (vllm:num_requests_running) - min by(job) (vllm:num_requests_running)` |
| **Requests scheduled per rank port** | `sum by(port) (rate(llm_d_epp_scheduler_attempts_total{status="success"}[5m]))` |
| **Active requests by role** | `sum by(job) (vllm:num_requests_running)` |
| **KV cache utilization by role** | `avg by(job) (vllm:kv_cache_usage_perc)` |
| **DisaggregatedSet revision gating share** | `llm_d_epp_disaggregatedset_revision_gating_share` |
| **Strict header selections with no match in 5m** | `sum(increase(llm_d_epp_disaggregatedset_strict_header_no_match_total[5m]))` |

For KV transfer between the prefill and decode roles, use the NIXL queries in [Prefill/Decode Disaggregation](#prefilldecode-disaggregation).

### Flow Control

Requires the `flowControl` feature gate enabled on the EPP.

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Queue size** | `sum(llm_d_epp_flow_control_queue_size)` |
| **Queue size by priority** | `sum by(priority) (llm_d_epp_flow_control_queue_size)` |
| **Queue wait time P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Pool saturation** | `llm_d_epp_flow_control_pool_saturation` |

## Notes

**Metric name prefixes:** Current deployments use `llm_d_epp_*`. Older deployments may use `llm_d_inference_scheduler_*`, `inference_objective_*`, `inference_pool_*`, or `inference_extension_*` — update accordingly if panels show "No data".

**Histograms:** Always include `by(le)` when using `histogram_quantile()`:

```promql
histogram_quantile(0.99, sum by(le) (rate(metric_name_bucket[5m])))
```

**Error metrics** only appear after the first error occurs. Use the [traffic generation script](../../../guides/recipes/observability/generate-traffic-basic.sh) to populate them for testing.
