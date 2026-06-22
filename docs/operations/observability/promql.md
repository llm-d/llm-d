# PromQL Query Reference

Ready-to-use PromQL queries for monitoring llm-d deployments. Use these in the Prometheus UI or as the basis for Grafana panels.

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
| **Inter-Token Latency P99** | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:inter_token_latency_seconds_bucket[5m])))` |
| **Request Rate** | `sum by(model_name) (rate(llm_d_epp_request_total[5m]))` |
| **GPU Utilization** | `avg by(gpu, node) (DCGM_FI_DEV_GPU_UTIL or nvidia_gpu_duty_cycle)` |
| **EPP E2E Latency P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_scheduler_e2e_duration_seconds_bucket[5m])))` |
| **EPP Plugin Latency P99** | `histogram_quantile(0.99, sum by(le, plugin_type) (rate(llm_d_epp_plugin_duration_seconds_bucket[5m])))` |

## Tier 2: Diagnostic Drill-Down

### Basic Model Serving

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **KV Cache Utilization** | `avg by(pod, model_name) (vllm:kv_cache_usage_perc)` |
| **Request Queue Depth** | `sum by(pod, model_name) (vllm:num_requests_waiting)` |
| **Active Requests** | `avg by(pod) (vllm:num_requests_running)` |
| **Total Throughput** (tokens/sec) | `sum by(model_name, pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Generation Token Rate** | `sum by(model_name, pod) (rate(vllm:generation_tokens_total[5m]))` |

### Routing & Load Balancing

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **QPS per pod** | `sum by(pod) (rate(llm_d_epp_request_total[5m]))` |
| **Token distribution per pod** | `sum by(pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Routing decision latency P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_plugin_duration_seconds_bucket[5m])))` |

### Prefix Caching

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Cache hit rate** | `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))` |
| **Per-pod hit rate** | `sum by(pod) (rate(vllm:prefix_cache_hits_total[5m])) / sum by(pod) (rate(vllm:prefix_cache_queries_total[5m]))` |
| **EPP prefix indexer size** | `llm_d_epp_prefix_indexer_size` |
| **EPP prefix hit ratio P90** | `histogram_quantile(0.90, sum by(le) (rate(llm_d_epp_prefix_indexer_hit_ratio_bucket[5m])))` |

### Prefill/Decode Disaggregation

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Prefill worker utilization** | `avg by(pod) (vllm:num_requests_running{pod=~".*prefill.*"})` |
| **Decode KV cache utilization** | `avg by(pod) (vllm:kv_cache_usage_perc{pod=~".*decode.*"})` |
| **P/D decision ratio** | `sum(rate(llm_d_epp_pd_decision_total{decision_type="prefill-decode"}[5m])) / sum(rate(llm_d_epp_pd_decision_total[5m]))` |

### Flow Control

Requires the `flowControl` feature gate enabled on the EPP.

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Queue size** | `sum(llm_d_epp_flow_control_queue_size)` |
| **Queue size by priority** | `sum by(priority) (llm_d_epp_flow_control_queue_size)` |
| **Queue wait time P99** | `histogram_quantile(0.99, sum by(le) (rate(llm_d_epp_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Pool saturation** | `llm_d_epp_flow_control_pool_saturation` |

## Notes

**Metric name prefixes:** Current deployments use `llm_d_epp_*`. Older deployments may use `llm_d_router_epp_*`, `inference_objective_*` or `inference_extension_*` — update accordingly if panels show "No data".

**Histograms:** Always include `by(le)` when using `histogram_quantile()`:
```promql
histogram_quantile(0.99, sum by(le) (rate(metric_name_bucket[5m])))
```

**Error metrics** only appear after the first error occurs. Use the [traffic generation script](../../../guides/recipes/observability/generate-traffic-basic.sh) to populate them for testing.
