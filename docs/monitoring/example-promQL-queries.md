# Example PromQL Queries for LLM-D Monitoring

This document provides PromQL queries for monitoring LLM-D deployments using Prometheus metrics.
The provided [load generation script](./scripts/generate-traffic-basic.sh) will populate error metrics for testing.

## Tier 1: Immediate Failure & Saturation Indicators

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **Overall Error Rate** (Platform-wide) | Fraction of all inference requests that resulted in errors, across all models. A non-zero value indicates platform-level issues. | `sum(rate(inference_objective_request_error_total[5m])) / sum(rate(inference_objective_request_total[5m]))` |
| **Per-Model Error Rate** | Error rate broken down by model, useful for isolating which model is degraded. | `sum by(model_name) (rate(inference_objective_request_error_total[5m])) / sum by(model_name) (rate(inference_objective_request_total[5m]))` |
| **Request Preemptions** (per vLLM instance) | Cumulative rate of request preemptions per vLLM pod. High preemption rates indicate memory pressure forcing in-progress requests to be evicted. | `sum by(pod, instance) (rate(vllm:num_preemptions_total[5m]))` |
| **Overall Latency P90** | 90th percentile end-to-end response latency measured at the gateway, across all models. | `histogram_quantile(0.90, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Overall Latency P99** | 99th percentile end-to-end response latency. Tail latency indicator for SLO compliance. | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Overall Latency P50** | Median end-to-end response latency. Represents the typical user experience. | `histogram_quantile(0.50, sum by(le) (rate(inference_objective_request_duration_seconds_bucket[5m])))` |
| **Model-Specific TTFT P99** (vLLM) | 99th percentile time-to-first-token measured at the vLLM engine, per model. Critical for streaming latency SLOs. | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))` |
| **Gateway TTFT P99** | 99th percentile time-to-first-token measured at the gateway (EPP), includes routing overhead on top of vLLM TTFT. | `histogram_quantile(0.99, sum by(le, model_name) (rate(inference_objective_request_ttft_seconds_bucket[5m])))` |
| **Model-Specific Inter-Token Latency P99** | 99th percentile inter-token latency per model. High values indicate decode-phase bottlenecks. | `histogram_quantile(0.99, sum by(le, model_name) (rate(vllm:inter_token_latency_seconds_bucket[5m])))` |
| **Gateway TPOT P99** | 99th percentile time-per-output-token measured at the gateway, per model. | `histogram_quantile(0.99, sum by(le, model_name) (rate(inference_objective_request_tpot_seconds_bucket[5m])))` |
| **SLO Violations** | Rate of SLO violations by model and violation type (e.g., TTFT or TPOT exceeded). Directly measures user-facing quality. | `sum by(model_name, type) (rate(inference_objective_request_slo_violation_total[5m]))` |
| **Scheduler Health** | Fraction of time the EPP scheduling pod is up. Values below 1.0 indicate instability. | `avg_over_time(up{job="gaie-inference-scheduling-epp"}[5m])` |
| **Scheduler Error Rate** | Error rate at the scheduling layer. Same as overall error rate but scoped to scheduler-routed traffic. | `sum(rate(inference_objective_request_error_total[5m])) / sum(rate(inference_objective_request_total[5m]))` |
| **Scheduler Error Rate by Type** | Error breakdown by error code to distinguish between 4xx (client) and 5xx (server) failures. | `sum by(error_code) (rate(inference_objective_request_error_total[5m]))` |
| **Scheduling Attempt Success Rate** | Ratio of successful vs failed scheduling attempts. Low success rates indicate routing problems. | `sum by(status) (rate(inference_extension_scheduler_attempts_total[5m]))` |
| **GPU Utilization** | Average GPU compute utilization per device and node. Values near 100% indicate GPU saturation. | `avg by(gpu, node) (DCGM_FI_DEV_GPU_UTIL or nvidia_gpu_duty_cycle)` |
| **Request Rate** | Queries per second by model and target model. Tracks demand and can reveal traffic shifts. | `sum by(model_name, target_model_name) (rate(inference_objective_request_total{}[5m]))` |
| **Running Requests** | Current number of in-flight requests per model at the gateway. | `sum by(model_name) (inference_objective_running_requests)` |
| **Pool Ready Pods** | Number of pods in the inference pool that are ready to serve traffic. Drop indicates scaling or health issues. | `inference_pool_ready_pods` |
| **EPP E2E Latency P99** | 99th percentile end-to-end scheduling latency within the EPP (filter → score → pick). High values indicate scheduling bottlenecks. | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_scheduler_e2e_duration_seconds_bucket[5m])))` |
| **Plugin Processing Latency** | 99th percentile plugin execution time by plugin type. Identifies slow scheduler plugins. | `histogram_quantile(0.99, sum by(le, plugin_type) (rate(inference_extension_plugin_duration_seconds_bucket[5m])))` |

## Tier 2: Diagnostic Drill-Down

### Path A: Basic Model Serving & Scaling

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **KV Cache Utilization** (per pod) | KV-cache usage percentage per pod and model. 1.0 = fully utilized; sustained high values lead to preemptions. | `avg by(pod, model_name) (vllm:kv_cache_usage_perc)` |
| **Pool Avg KV Cache Utilization** | Average KV-cache utilization across all pods in the inference pool. Pool-level view for capacity planning. | `avg by(name) (inference_pool_average_kv_cache_utilization)` |
| **Request Queue Lengths** (per pod) | Number of requests waiting in the vLLM engine queue per pod. Growth indicates the pod can't keep up with demand. | `sum by(pod, model_name) (vllm:num_requests_waiting)` |
| **Pool Avg Queue Size** | Average pending request queue size across the pool. Pool-level congestion indicator. | `avg by(name) (inference_pool_average_queue_size)` |
| **Per-Pod Queue Size** | Queue depth for each individual model server pod. Identifies hot pods. | `sum by(name, model_server_pod) (inference_pool_per_pod_queue_size)` |
| **Model Throughput** (Tokens/sec) | Total token throughput (prompt + generation) per model and pod. Key capacity metric. | `sum by(model_name, pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Generation Token Rate** | Output token generation rate per model and pod. Measures decode throughput specifically. | `sum by(model_name, pod) (rate(vllm:generation_tokens_total[5m]))` |
| **Queue Utilization** | Average number of requests actively running per pod. Represents compute saturation. | `avg by(pod) (vllm:num_requests_running)` |
| **Input Token Distribution P99** | 99th percentile input token count per request, measured at the gateway. Identifies unexpectedly large prompts. | `histogram_quantile(0.99, sum by(le, model_name) (rate(inference_objective_input_tokens_bucket[5m])))` |
| **Output Token Distribution P99** | 99th percentile output token count per request. Helps size decode capacity. | `histogram_quantile(0.99, sum by(le, model_name) (rate(inference_objective_output_tokens_bucket[5m])))` |
| **Request Phase: Queue Wait Time P99** | 99th percentile time a request spends waiting in the vLLM queue before execution begins. | `histogram_quantile(0.99, sum by(le) (rate(vllm:request_queue_time_seconds_bucket[5m])))` |
| **Request Phase: Prefill Time P99** | 99th percentile time spent in the prefill phase. Long prefill times suggest large prompts or insufficient compute. | `histogram_quantile(0.99, sum by(le) (rate(vllm:request_prefill_time_seconds_bucket[5m])))` |
| **Request Phase: Decode Time P99** | 99th percentile time spent in the decode phase. Correlates with output length and decode throughput. | `histogram_quantile(0.99, sum by(le) (rate(vllm:request_decode_time_seconds_bucket[5m])))` |
| **Successful Requests by Finish Reason** | Rate of completed requests grouped by finish reason (stop, length, abort). Helps understand request termination patterns. | `sum by(finished_reason) (rate(vllm:request_success_total[5m]))` |

### Path B: Intelligent Routing & Load Balancing

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **Request Distribution** (QPS per instance) | Per-pod request rate for targeted traffic. Uneven distribution indicates routing imbalance. | `sum by(pod) (rate(inference_objective_request_total{target_model_name!=""}[5m]))` |
| **Token Distribution** | Per-pod token throughput. Complements request distribution to reveal skew from variable prompt sizes. | `sum by(pod) (rate(vllm:prompt_tokens_total[5m]) + rate(vllm:generation_tokens_total[5m]))` |
| **Idle GPU Time** | Fraction of time a pod has zero tokens in its batch. High idle time indicates underutilization or poor routing. | `1 - clamp_max(rate(vllm:iteration_tokens_total_count[5m]), 1)` |
| **Routing Decision Latency** | 99th percentile plugin processing time for routing decisions. Measures overhead of the scoring/filtering step. | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_plugin_duration_seconds_bucket[5m])))` |
| **TTFT Prediction Accuracy** | Predicted vs actual TTFT distributions. Divergence suggests the prediction model needs retraining. | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_predicted_ttft_seconds_bucket[5m])))` |
| **TPOT Prediction Accuracy** | Predicted vs actual TPOT distributions. Compare with actual TPOT to evaluate prediction quality. | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_predicted_tpot_seconds_bucket[5m])))` |
| **TTFT Prediction Duration P99** | Time taken to generate TTFT predictions. High values add scheduling latency. | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_ttft_prediction_duration_seconds_bucket[5m])))` |
| **TPOT Prediction Duration P99** | Time taken to generate TPOT predictions. High values add scheduling latency. | `histogram_quantile(0.99, sum by(le) (rate(inference_objective_request_tpot_prediction_duration_seconds_bucket[5m])))` |
| **Normalized Time Per Output Token P99** | Latency divided by number of output tokens. Normalizes for output length to compare across request sizes. | `histogram_quantile(0.99, sum by(le, model_name) (rate(inference_objective_normalized_time_per_output_token_seconds_bucket[5m])))` |
| **Model Rewrite Decisions** | Rate of model rewrite routing decisions. Tracks how often the EPP rewrites the target model. | `sum by(model_rewrite_name, target_model) (rate(inference_extension_model_rewrite_decisions_total[5m]))` |

### Path C: Prefix Caching

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **Prefix Cache Hit Rate** (vLLM) | Global cache effectiveness: fraction of queried prefix tokens found in cache. Higher is better for latency. | `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))` |
| **Per-Instance Hit Rate** (vLLM) | Per-pod cache hit rate. Uneven hit rates may indicate poor prefix-aware routing. | `sum by(pod) (rate(vllm:prefix_cache_hits_total[5m])) / sum by(pod) (rate(vllm:prefix_cache_queries_total[5m]))` |
| **External Prefix Cache Hit Rate** | Cross-instance KV connector cache hit rate. Measures effectiveness of distributed prefix sharing. | `sum(rate(vllm:external_prefix_cache_hits_total[5m])) / sum(rate(vllm:external_prefix_cache_queries_total[5m]))` |
| **Prompt Tokens by Source** | Breakdown of prompt tokens by source: local compute, local cache hit, or external KV transfer. Shows where tokens come from. | `sum by(source) (rate(vllm:prompt_tokens_by_source_total[5m]))` |
| **Cached Prompt Tokens Rate** | Rate of prompt tokens served from cache (local + external). Higher means more computation saved. | `sum by(pod) (rate(vllm:prompt_tokens_cached_total[5m]))` |
| **Prompt Cached Tokens Distribution** (Gateway) | Distribution of cached token counts per request at the gateway level. | `histogram_quantile(0.50, sum by(le, model_name) (rate(inference_objective_prompt_cached_tokens_bucket[5m])))` |
| **Cache Utilization** (% full) | KV cache percentage utilization per pod. High sustained values indicate cache pressure. | `avg by(pod, model_name) (vllm:kv_cache_usage_perc * 100)` |
| **EPP Prefix Indexer Size** | Number of entries in the EPP-side prefix index. Tracks growth of the routing-level cache index. | `inference_extension_prefix_indexer_size` |
| **EPP Prefix Indexer Hit Ratio P50** | Median ratio of prefix length matched to total prefix length. Measures how much of the prefix the EPP can match. | `histogram_quantile(0.50, sum by(le) (rate(inference_extension_prefix_indexer_hit_ratio_bucket[5m])))` |
| **EPP Prefix Indexer Hit Ratio P90** | 90th percentile of the prefix match ratio. | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_prefix_indexer_hit_ratio_bucket[5m])))` |
| **EPP Prefix Indexer Hit Bytes P50** | Median matched prefix length in bytes. Larger matches mean more cache reuse. | `histogram_quantile(0.50, sum by(le) (rate(inference_extension_prefix_indexer_hit_bytes_bucket[5m])))` |
| **EPP Prefix Indexer Hit Bytes P90** | 90th percentile of matched prefix length in bytes. | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_prefix_indexer_hit_bytes_bucket[5m])))` |

### Path D: Disaggregated Serving (P/D, E/P/D)

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **Prefill Worker Utilization** | Average active requests on prefill-role pods. High values indicate prefill saturation. | `avg by(pod) (vllm:num_requests_running{pod=~".*prefill.*"})` |
| **Decode Worker Utilization** | KV cache usage on decode-role pods. High values indicate decode-side memory pressure. | `avg by(pod) (vllm:kv_cache_usage_perc{pod=~".*decode.*"})` |
| **Prefill Queue Length** | Waiting requests on prefill pods. Growth means prefill is the bottleneck. | `sum by(pod) (vllm:num_requests_waiting{pod=~".*prefill.*"})` |
| **Disagg Decision Rate** | Rate of disaggregation routing decisions by type (decode-only, prefill-decode, encode-decode, encode-prefill-decode). Shows the routing mix. | `sum by(decision_type) (rate(llm_d_inference_scheduler_disagg_decision_total[5m]))` |
| **Decode-Only Request Rate** | Rate of requests routed directly to decode without separate prefill. | `sum(rate(llm_d_inference_scheduler_disagg_decision_total{decision_type="decode-only"}[5m]))` |
| **Prefill-Decode Request Rate** | Rate of requests routed through P/D or EP/D disaggregation. | `sum(rate(llm_d_inference_scheduler_disagg_decision_total{decision_type="prefill-decode"}[5m]))` |
| **Encode-Decode Request Rate** | Rate of requests routed through E/PD (encode then prefill+decode on same worker). | `sum(rate(llm_d_inference_scheduler_disagg_decision_total{decision_type="encode-decode"}[5m]))` |
| **Encode-Prefill-Decode Request Rate** | Rate of requests routed through full E/P/D disaggregation (all three stages separate). | `sum(rate(llm_d_inference_scheduler_disagg_decision_total{decision_type="encode-prefill-decode"}[5m]))` |
| **Disagg Ratio** | Fraction of requests using prefill-decode disaggregation vs all decisions. Measures disagg adoption. | `sum(rate(llm_d_inference_scheduler_disagg_decision_total{decision_type="prefill-decode"}[5m])) / sum(rate(llm_d_inference_scheduler_disagg_decision_total[5m]))` |
| **Prefill Time P99** (per pod) | 99th percentile time in prefill phase on prefill pods. Identifies slow prefill workers. | `histogram_quantile(0.99, sum by(le, pod) (rate(vllm:request_prefill_time_seconds_bucket{pod=~".*prefill.*"}[5m])))` |
| **Decode Time P99** (per pod) | 99th percentile time in decode phase on decode pods. Identifies slow decode workers. | `histogram_quantile(0.99, sum by(le, pod) (rate(vllm:request_decode_time_seconds_bucket{pod=~".*decode.*"}[5m])))` |
| **KV Offload Throughput** | Total bytes offloaded by KV connector per transfer type (e.g., cpu_to_gpu, gpu_to_cpu). Tracks KV transfer volume. | `sum by(transfer_type) (rate(vllm:kv_offload_total_bytes_total[5m]))` |
| **KV Offload Latency** | Total time spent on KV offloading operations by transfer type. Identifies transfer bottlenecks. | `sum by(transfer_type) (rate(vllm:kv_offload_total_time_total[5m]))` |
| **KV Offload Transfer Size P99** | 99th percentile KV offload transfer size in bytes. Large transfers may cause latency spikes. | `histogram_quantile(0.99, sum by(le, transfer_type) (rate(vllm:kv_offload_size_bucket[5m])))` |

### Path E: Flow Control & Request Queuing (requires the flow control FeatureGate enabled with EPP)

| Metric Need | Description | PromQL Query |
| ----------- | ----------- | ------------ |
| **Flow Control Queue Size** | Total requests currently queued in the EPP flow control layer. Growth indicates backpressure. | `sum(inference_extension_flow_control_queue_size)` |
| **Flow Control Queue Size by Priority** | Queue depth broken out by priority level. Identifies if low-priority traffic is starving. | `sum by(priority) (inference_extension_flow_control_queue_size)` |
| **Flow Control Queue Bytes** | Total bytes associated with queued requests by fairness group. Tracks memory pressure in the queue. | `sum by(fairness_id) (inference_extension_flow_control_queue_bytes)` |
| **Pool Saturation** | Current saturation level of the inference pool (0.0 = empty, 1.0 = fully saturated). Direct capacity indicator. | `inference_extension_flow_control_pool_saturation` |
| **Flow Control Request Queue Duration P99** | 99th percentile time requests spend in the flow control queue. High values mean long admission delays. | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Flow Control Request Queue Duration P90** | 90th percentile flow control queue time. | `histogram_quantile(0.90, sum by(le) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Flow Control Request Queue Duration by Outcome** | Queue duration P99 split by outcome (admitted vs rejected). Reveals if rejected requests waited long before being dropped. | `histogram_quantile(0.99, sum by(le, outcome) (rate(inference_extension_flow_control_request_queue_duration_seconds_bucket[5m])))` |
| **Dispatch Cycle Duration P99** | 99th percentile time for each flow control dispatch cycle. Measures internal scheduling loop overhead. | `histogram_quantile(0.99, sum by(le) (rate(inference_extension_flow_control_dispatch_cycle_duration_seconds_bucket[5m])))` |
| **Enqueue Duration P99** | 99th percentile time to enqueue requests by priority and outcome. High values indicate contention in the queue. | `histogram_quantile(0.99, sum by(le, priority, outcome) (rate(inference_extension_flow_control_request_enqueue_duration_seconds_bucket[5m])))` |

## Key Notes

### Metric Name Updates

- **GAIE Metrics**: Current metric names use `inference_objective_*` prefix (older deployments may still use `inference_model_*`)
- **vLLM Metrics**: Inter-token latency metrics use `vllm:inter_token_latency_seconds` (previously `vllm:time_per_output_token_seconds`)
- **Disagg Metrics**: `llm_d_inference_scheduler_pd_decision_total` is deprecated; use `llm_d_inference_scheduler_disagg_decision_total` which covers all disagg stages (decode-only, prefill-decode, encode-decode, encode-prefill-decode)

### Histogram Queries

- Always include `by(le)` grouping when using `histogram_quantile()` with bucket metrics
- Example: `histogram_quantile(0.99, sum by(le) (rate(metric_name_bucket[5m])))`

### Job Labels

- EPP availability queries use job labels like `job="gaie-inference-scheduling-epp"`
- Actual job names depend on your deployment configuration

### Error Metrics

- Error metrics (`*_error_total`) only appear after the first error occurs
- Use the provided [load generation script](./scripts/generate-traffic-basic.sh) to populate error metrics for testing

### Metric Sources

| Source | Metric Prefix | Description |
| ------ | ------------- | ----------- |
| vLLM engine | `vllm:` | Model server metrics (cache, tokens, latencies, preemptions) |
| Gateway API Inference Extension (EPP) | `inference_objective_`, `inference_pool_`, `inference_extension_` | Gateway-level request metrics, pool health, scheduling, flow control |
| llm-d inference scheduler | `llm_d_inference_scheduler_` | Disaggregation routing decisions |
| DCGM / NVIDIA | `DCGM_FI_DEV_GPU_UTIL`, `nvidia_gpu_duty_cycle` | GPU hardware utilization |

## Missing Metrics (Require Additional Instrumentation)

The following metrics are not currently available and would need custom instrumentation:

### Path C: Prefix Caching

- **Prefix Cache Memory Usage (Absolute)**: Only percentage utilization is available via `vllm:kv_cache_usage_perc`
- **Cache Eviction Rate**: No direct eviction counter exists. KV cache residency metrics are available when `--kv-cache-metrics-enabled` is set: `vllm:kv_block_lifetime_seconds`, `vllm:kv_block_idle_before_evict_seconds`, `vllm:kv_block_reuse_gap_seconds`

### Workarounds

- **Cache Pressure Detection**: Monitor trends in `vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total` — declining hit rates may indicate cache evictions
- **Transfer Bottlenecks**: Use `vllm:kv_offload_total_bytes_total` and `vllm:kv_offload_total_time_total` to monitor KV transfer throughput and latency between disaggregated workers
