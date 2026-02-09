# Example PromQL Queries for LLM-D Monitoring

This document provides PromQL queries for monitoring LLM-D deployments using Prometheus metrics.
The provided [load generation script](./scripts/generate-load-llmd.sh) will populate error metrics for testing.

## Tier 1: Immediate Failure & Saturation Indicators

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Overall Error Rate** (Platform-wide) | `sum(rate(inference_objective_request_error_total[5m])) / sum(rate(inference_objective_request_total[5m]))` |
| **Per-Model Error Rate** | `sum by(model_name) (rate(inference_objective_request_error_total[5m])) / sum by(model_name) (rate(inference_objective_request_total[5m]))` |
| **Request Preemptions** (per vLLM instance) | `sum by(pod, instance) (rate(vllm:num_preemptions[5m]))` |
| **Overall Latency P90** | `histogram_quantile(0.90, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Overall Latency P99** | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Overall Latency P50** | `histogram_quantile(0.50, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Model-Specific TTFT P99** | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))` |
| **Model-Specific Inter-Token Latency P99** | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:inter_token_latency_seconds_bucket[5m])))` |
| **Scheduler Health** | `avg_over_time(up{job="gaie-inference-scheduling-epp"}[5m])` |
| **Scheduler Error Rate** | `sum(rate(inference_objective_request_error_total[5m])) / sum(rate(inference_objective_request_total[5m]))` |
| **Scheduler Error Rate by Type** | `sum by(error_code) (rate(inference_objective_request_error_total[5m]))` |
| **GPU Utilization** | `avg by(gpu, node) (DCGM_FI_DEV_GPU_UTIL or nvidia_gpu_duty_cycle)` |
| **Request Rate** | `sum by(model_name, target_model_name) (rate(inference_objective_request_total{}[5m]))` |
| **EPP E2E Latency P99** | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_scheduler_e2e_duration_seconds_bucket[5m])))` |
| **Plugin Processing Latency** | `histogram_quantile(0.99, sum by(le, plugin_type) (rate(inference_extension_plugin_duration_seconds_bucket[5m])))` |

## Tier 2: Diagnostic Drill-Down

### Path A: Basic Model Serving & Scaling

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **KV Cache Utilization** | `avg by(pod, model_name) (vllm:kv_cache_usage_perc)` |
| **Request Queue Lengths** | `sum by(pod, model_name) (vllm:num_requests_waiting)` |
| **Model Throughput** (Tokens/sec) | `sum by(model_name, pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Generation Token Rate** | `sum by(model_name, pod) (rate(vllm:generation_tokens_total[5m]))` |
| **Queue Utilization** | `avg by(pod) (vllm:num_requests_running)` |

### Path B: Intelligent Routing & Load Balancing

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Request Distribution** (QPS per instance) | `sum by(pod) (rate(inference_objective_request_total{target_model!=""}[5m]))` |
| **Token Distribution** | `sum by(pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Idle GPU Time** | `1 - avg by(pod) (rate(vllm:iteration_tokens_total_count[5m]) > 0)` |
| **Routing Decision Latency** | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_scheduler_plugin_duration_seconds_bucket[5m])))` |

### Path C: Prefix Caching

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Prefix Cache Hit Rate** (vLLM) | `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))` |
| **Per-Instance Hit Rate** (vLLM) | `sum by(pod) (rate(vllm:prefix_cache_hits_total[5m])) / sum by(pod) (rate(vllm:prefix_cache_queries_total[5m]))` |
| **Cache Utilization** (% full) | `avg by(pod, model_name) (vllm:kv_cache_usage_perc * 100)` |
| **EPP Prefix Indexer Size** | `inference_extension_prefix_indexer_size` |
| **EPP Prefix Indexer Hit Ratio P50** | `histogram_quantile(0.50, sum by(le) (rate(inference_extension_prefix_indexer_hit_ratio_bucket[5m])))` |
| **EPP Prefix Indexer Hit Ratio P90** | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_prefix_indexer_hit_ratio_bucket[5m])))` |
| **EPP Prefix Indexer Hit Bytes P50** | `histogram_quantile(0.50, sum by(le) (rate(inference_extension_prefix_indexer_hit_bytes_bucket[5m])))` |
| **EPP Prefix Indexer Hit Bytes P90** | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_prefix_indexer_hit_bytes_bucket[5m])))` |

### Path D: P/D Disaggregation

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Prefill Worker Utilization** | `avg by(pod) (vllm:num_requests_running{pod=~".*prefill.*"})` |
| **Decode Worker Utilization** | `avg by(pod) (vllm:kv_cache_usage_perc{pod=~".*decode.*"})` |
| **Prefill Queue Length** | `sum by(pod) (vllm:num_requests_waiting{pod=~".*prefill.*"})` |
| **P/D Decision Rate** | `sum by(decision_type) (rate(llm_d_inference_scheduler_pd_decision_total[5m]))` |
| **Decode-Only Request Rate** | `sum(rate(llm_d_inference_scheduler_pd_decision_total{decision_type="decode-only"}[5m]))` |
| **Prefill-Decode Request Rate** | `sum(rate(llm_d_inference_scheduler_pd_decision_total{decision_type="prefill-decode"}[5m]))` |
| **P/D Decision Ratio** | `sum(rate(llm_d_inference_scheduler_pd_decision_total{decision_type="prefill-decode"}[5m])) / sum(rate(llm_d_inference_scheduler_pd_decision_total[5m]))` |

### Path E: Flow Control & Request Queuing (requires the flow control FeatureGate enabled with EPP)

| Metric Need | PromQL Query |
| ----------- | ------------ |
| **Flow Control Queue Size** | `sum(inference_extension_flow_control_queue_size)` |
| **Flow Control Queue Size by Priority** | `sum by(priority) (inference_extension_flow_control_queue_size)` |
| **Flow Control Request Queue Duration P99** | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Flow Control Request Queue Duration P90** | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Flow Control Request Queue Duration by Outcome** | `histogram_quantile(0.99, sum by(le, outcome) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |

## Key Notes

### Metric Name Updates

- **GAIE Metrics**: Current metric names use `inference_objective_*` prefix (older deployments may still use `inference_model_*`)
- **vLLM Metrics**: Inter-token latency metrics use `vllm:inter_token_latency_seconds` (previously `vllm:time_per_output_token_seconds`)

### Histogram Queries

- Always include `by(le)` grouping when using `histogram_quantile()` with bucket metrics
- Example: `histogram_quantile(0.99, sum by(le) (rate(metric_name_bucket[5m])))`

### Job Labels

- EPP availability queries use job labels like `job="gaie-inference-scheduling-epp"`
- Actual job names depend on your deployment configuration

### Error Metrics

- Error metrics (`*_error_total`) only appear after the first error occurs
- Use the provided [load generation script](./scripts/generate-load-llmd.sh) to populate error metrics for testing

## Missing Metrics (Require Additional Instrumentation)

The following metrics from community-gathered monitoring requirements are not currently available and would need custom instrumentation:

### Path C: Prefix Caching

- **Cache Eviction Rate**: No metrics track when cache entries are evicted due to memory pressure
- **Prefix Cache Memory Usage (Absolute)**: Only percentage utilization is available

### Path D: P/D Disaggregation

- **KV Cache Transfer Times**: No metrics track the latency of transferring KV cache between prefill and decode workers

### Workarounds

- **Cache Pressure Detection**: Monitor trends in `vllm:prefix_cache_hits` / `vllm:prefix_cache_queries` - declining hit rates may indicate cache evictions
- **Transfer Bottlenecks**: Monitor overall latency spikes during P/D operations as an indirect indicator
