# Router (EPP) Metrics

Reference for the Prometheus metrics the llm-d Router's Endpoint Picker (EPP) exports. To scrape them, see [Collect Metrics](./metrics.md); for ready-to-run queries, see the [PromQL Query Reference](./promql.md), and for alerts built on them, see [Configure Alerts](./alerting.md).

The EPP exposes Prometheus metrics under the `llm_d_epp_` prefix.

## Request and Latency

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_request_total` | Total request count per flow ID and priority | Baseline for calculating error rate and throughput per model |
| `llm_d_epp_request_error_total` | Error count per flow ID and priority | Rising errors signal backend failures. Alert when error rate exceeds 5% |
| `llm_d_epp_request_duration_seconds` | Response latency distribution per flow ID and priority | The SLO metric. Tracks full round-trip time from request to response |
| `llm_d_epp_request_size_bytes` | Incoming request size distribution in bytes per flow ID and priority | Helps identify payload size anomalies and exceptionally large incoming prompts |
| `llm_d_epp_response_size_bytes` | Outgoing response size distribution in bytes per flow ID and priority | Tracks outgoing bandwidth usage and response payload size distribution |
| `llm_d_epp_request_input_tokens` | Input token count distribution per flow ID and priority | Helps identify expensive requests. Long prompts cost more compute |
| `llm_d_epp_request_output_tokens` | Output token count distribution per flow ID and priority | Combined with duration, gives normalized cost and generation volume per token |
| `llm_d_epp_request_cached_tokens` | Cached prompt token distribution per flow ID and priority | Measures prefix cache utilization reported by model servers |
| `llm_d_epp_request_running` | Active request count per flow ID and priority | Shows real-time load concurrency across models |
| `llm_d_epp_request_ntpot_seconds` | Normalized time per output token (NTPOT) distribution per flow ID and priority | Key efficiency metric (lower is better). Compare across pods to find stragglers |
| `llm_d_epp_request_ttft_seconds` | Time to first token (TTFT) distribution per flow ID and priority | Directly measures user-perceived responsiveness and time to initial output byte |
| `llm_d_epp_request_streaming_tpot_seconds` | Time per output token (TPOT) distribution per flow ID and priority; applicable to streaming requests | Tracks ongoing generation speed excluding initial prompt prefill latency |
| `llm_d_epp_request_streaming_itl_seconds` | Inter-token latency (ITL) distribution per flow ID and priority; applicable to streaming requests | Measures pacing between consecutive response body chunks; spikes indicate choppy output |

## Inference Pool

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_ready_endpoints` | Number of ready endpoints in the pool | If this drops below expected count, pods are crashing or not scheduling |
| `llm_d_epp_average_kv_cache_utilization` | Mean KV cache utilization across the pool | High average utilization signals the pool is nearing memory capacity |
| `llm_d_epp_average_queue_size` | Mean queue depth across model servers | Primary signal for pool saturation and autoscaling decisions |
| `llm_d_epp_average_running_requests` | Mean running requests across model servers | Tracks concurrency across endpoints |
| `llm_d_epp_std_dev_kv_cache_utilization` | Spread of KV cache utilization across endpoints | High standard deviation indicates uneven cache distribution |
| `llm_d_epp_std_dev_queue_size` | Spread of queue depth across endpoints | High variance points to routing imbalances |
| `llm_d_epp_std_dev_running_requests` | Spread of in-flight requests across endpoints | Helps diagnose stragglers or hotspotting |
| `llm_d_epp_per_endpoint_queue_size` | Per-endpoint queue depth | Identifies overloaded model-server replicas |

## Scheduler, Plugins & Processing Overhead

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_scheduler_attempts_total` | Scheduling attempt counts and outcomes | Track failed scheduling attempts. High failure rate indicates filter/scorer misconfiguration |
| `llm_d_epp_scheduler_e2e_duration_seconds` | End-to-end scheduling latency distribution | Tracks latency spent within the EPP scheduling cycle |
| `llm_d_epp_plugin_duration_seconds` | Per-plugin processing latency distribution | Identifies slow or bottlenecked scheduling plugins |
| `llm_d_epp_plugin_data_scope_violations_total` | Total endpoint attribute accesses rejected due to undeclared DataKeys | Non-zero value indicates plugin implementation drift from declaration |
| `llm_d_epp_request_processing_duration_seconds` | Time from request receipt until request body has been handled | Measures EPP request ingest and admission overhead |
| `llm_d_epp_response_processing_duration_seconds` | EPP response processing latency | Measures response streaming handler overhead |
| `llm_d_epp_info` | Build information (`commit`, `build_ref`) | Verifies running binary release and build details |
| `llm_d_epp_model_rewrite_decisions_total` | Total model rewrite decisions | Tracks request rewrites across configured rewrite rules |

## In-Flight Load

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_inflight_requests` | Requests currently in flight on each endpoint | Per-replica queue depth for load-aware routing and capacity analysis |
| `llm_d_epp_inflight_tokens` | Tokens currently in flight on each endpoint | Per-replica token pressure, a finer load signal than request count |

## Disaggregation & Rollout

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_disagg_decision_total` | Routing decisions across disaggregation stages | Tracks breakdown of decode-only, prefill-decode, encode-decode, and full pipeline decisions |
| `llm_d_epp_pd_decision_total` | Prefill/decode disaggregation decisions (deprecated handler) | Backward-compatible counter for P/D routing decisions |
| `llm_d_epp_disaggregatedset_strict_header_no_match_total` | Strict header selections that matched no endpoint | Surfaces strict selector misconfigurations that fail closed |
| `llm_d_epp_disaggregatedset_revision_gating_share` | Current weighted revision gating share (0 to 1) | Monitors canary and rolling deployment shares across disaggregated revisions |

## Flow Control

When flow control is enabled (`flowControl` feature gate), these additional metrics are exposed:

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_flow_control_queue_size` | Queued request count per flow ID and priority | Growing queue means the pool cannot keep up. Consider scaling or adjusting priority bands |
| `llm_d_epp_flow_control_queue_bytes` | Queued payload size in bytes per flow ID and priority | Large queued payloads can exhaust EPP memory. Monitor alongside `maxBytes` config |
| `llm_d_epp_flow_control_request_queue_duration_seconds` | Queuing duration distribution per flow ID and priority | Directly impacts user-perceived latency. High values mean flow control is holding requests too long |
| `llm_d_epp_flow_control_dispatch_cycle_duration_seconds` | Internal dispatch cycle duration distribution | Tracks execution speed of the flow control scheduler loop |
| `llm_d_epp_flow_control_request_enqueue_duration_seconds` | Request enqueue duration distribution per flow ID and priority | Measures admission overhead entering the flow control queue |
| `llm_d_epp_flow_control_pool_saturation` | Pool saturation level (0.0 to 1.0+) | Above 1.0 means demand exceeds capacity and flow control is actively throttling. Scale up or shed load |
| `llm_d_epp_flow_control_stale_endpoints` | Number of endpoints with missing or stale telemetry | Non-zero value indicates metrics collection issues rather than genuine overload |
| `llm_d_epp_flow_control_requests_total` | Total requests processed by flow control by outcome | Direct signal for rejection (`outcome="RejectedCapacity"`) and eviction rates |
| `llm_d_epp_flow_control_revocations_issued_total` | Total in-flight eviction revocations issued by priority | Tracks preemptive capacity reclamation events under contention |
| `llm_d_epp_flow_control_revocations_total` | Total in-flight eviction revocations by outcome (`confirmed`, `timed_out`) | Confirms stream cancellation success vs timeout |
| `llm_d_epp_flow_control_reclaim_target` | Last computed reclamation deficit | Measures capacity deficit driving revocations |
| `llm_d_epp_flow_control_pending_reclaim` | Outstanding and cooling pending-reclaim debits | Tracks in-flight reclamation debits before confirmation |
| `llm_d_epp_flow_control_revocation_confirmation_seconds` | Revocation issue to confirmed termination duration | Latency signal for in-flight eviction completion |
| `llm_d_epp_program_aware_jains_fairness_index` | Jain's fairness index across active programs | Quantifies fairness balance across competing programs |
| `llm_d_epp_program_aware_avg_wait_time_milliseconds` | Mean queue wait time per program in ms | Monitors per-program queuing delays |
| `llm_d_epp_program_aware_attained_service_tokens` | Time-decayed attained service per program | Tracks historical consumption used for Least Attained Service (LAS) scheduling |

## Prefix & Multimodal Cache

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_prefix_indexer_size` | Entries in the approximate prefix index | Tracks index memory footprint |
| `llm_d_epp_prefix_indexer_hit_ratio` | Prefix-match hit ratio distribution | High hit ratio indicates effective prefix caching |
| `llm_d_epp_prefix_indexer_hit_bytes` | Bytes matched per prefix lookup | Quantifies data reused from cache |
| `llm_d_epp_encoder_cache_queries_total` | Total multimodal encoder-cache lookups | Volume of multimodal items queried against cache |
| `llm_d_epp_encoder_cache_hits_total` | Encoder-cache hits per pod | Tracks multimodal embedding cache reuse |
| `llm_d_epp_encoder_cache_hit_ratio` | Hit ratio distribution for encoder-cache lookups | Surfaces uneven multimodal cache locality across pods |
| `llm_d_epp_prefix_predicted_cached_tokens` | Prompt tokens predicted to hit the chosen endpoint's prefix cache | Divide its `_sum` by `llm_d_epp_prefix_prompt_tokens_sum` for the hit rate the router predicted when routing |
| `llm_d_epp_prefix_prompt_tokens` | Prompt tokens the prefix predictions were measured against | Denominator for the predicted hit rate; compare against the delivered rate from `llm_d_epp_request_cached_tokens` / `llm_d_epp_request_input_tokens` |
| `llm_d_epp_token_producer_render_duration_seconds` | Duration of one render (tokenization) call by `result` | Render sits on the request path before routing, so its latency feeds TTFT directly |
| `llm_d_epp_token_producer_render_failures_total` | Failed render calls by `reason` (`timeout`, `status`, `connection`, ...) | A request whose render fails is routed without a tokenized prompt, so prefix-cache scoring is skipped for it |

## Precise KV-Cache Index & Events

These series belong to the precise-prefix-cache pipeline used by [precise prefix cache routing](../../../guides/precise-prefix-cache-routing/README.md). They are emitted only when `indexerConfig.kvBlockIndexConfig.enableMetrics: true` is set on the `precise-prefix-cache-producer` (the guide's values file sets it); they are absent otherwise.

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_kv_cache_index_admissions_total` | KV blocks admitted to the precise index | A flat counter under traffic means KV events are not populating the index, so the affinity filter routes blind |
| `llm_d_epp_kv_cache_index_evictions_total` | KV blocks evicted from the precise index | Compare with admissions; heavy eviction churn shrinks the reusable prefix window |
| `llm_d_epp_kv_cache_index_lookup_requests_total` | Index lookups performed | Volume of prefix lookups the router makes |
| `llm_d_epp_kv_cache_index_lookup_hits_total` | Contiguous prefix blocks matched by the best pod per lookup | Lookups without hits while events flow usually mean a block-size mismatch |
| `llm_d_epp_kv_cache_index_lookup_latency_seconds` | Index lookup latency | Lookup time is routing-decision overhead on every request |
| `llm_d_epp_kv_cache_events_active_subscribers` | ZMQ subscribers currently managed | Should equal the model server pod count; a lower value means some pods publish events nobody consumes |
| `llm_d_epp_kv_cache_events_messages_received_total` | Messages received per `pod_identifier` | A pod with zero rate is not delivering KV events to the router |
| `llm_d_epp_kv_cache_events_subscriber_reconnections_total` | ZMQ subscriber reconnection attempts per pod | Persistent reconnects point at network policy or endpoint configuration problems |
| `llm_d_epp_kv_cache_events_zmq_errors_total` | ZMQ subscriber errors by `pod_identifier` and `operation` | Direct error signal for the event transport |
| `llm_d_epp_kv_cache_events_pool_queue_depth` | Messages queued across event-pool workers | Approaching `pool_capacity` means event processing can't keep up and the index lags live cache state |
| `llm_d_epp_kv_cache_events_pool_capacity` | Event-pool worker capacity | Denominator for event-pool saturation |
| `llm_d_epp_kv_cache_events_stores_skipped_total` | KV store events skipped before indexing by `reason` | Events arriving but skipped never reach the index |
| `llm_d_epp_kv_cache_events_removals_skipped_total` | KV removal events skipped before indexing by `reason` | Skipped removals leave stale blocks in the index |

## Predicted Latency & SLO

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_request_predicted_ttft_seconds` | Predicted TTFT distribution | Used by latency-based scheduling to evaluate candidates |
| `llm_d_epp_request_ttft_prediction_duration_seconds` | Time spent computing TTFT predictions | Measures overhead of the predictor sidecar |
| `llm_d_epp_request_predicted_tpot_seconds` | Predicted TPOT distribution | Evaluates token generation latency across backends |
| `llm_d_epp_request_tpot_prediction_duration_seconds` | Time spent computing TPOT predictions | Measures TPOT predictor execution latency |
| `llm_d_epp_request_slo_violation_total` | Count of requests violating configured latency SLOs | Primary alert signal for SLO degradation |
| `llm_d_epp_inference_request_metric` | Consolidated gauge for observed request metrics and SLO states | Flexible single-series export of TTFT, TPOT, and SLO metrics |

## ext_proc Streams & Data Layer Errors

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_extproc_streams_inflight` | Number of open ext_proc gRPC streams (opt-in) | Monitors Envoy↔EPP gRPC stream connections |
| `llm_d_epp_extproc_stream_duration_seconds` | Duration ext_proc gRPC streams stay open | Sudden short durations indicate stream reconnect churn |
| `llm_d_epp_extproc_streams_total` | Completed ext_proc gRPC streams by status code | Surfaces abnormal stream disconnects and errors |
| `llm_d_epp_datalayer_poll_errors_total` | Data-source poll failures per source type | Signals failing telemetry scrapes from model servers |
| `llm_d_epp_datalayer_extract_errors_total` | Extractor failures per source/extractor type | Signals parsing failures on scraped telemetry payloads |
