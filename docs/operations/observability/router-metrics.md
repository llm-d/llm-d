# Router Metrics

Reference for the Prometheus metrics exported by the llm-d Router deployment components: the Endpoint Picker (EPP, `llm_d_epp_` prefix), the KV-Cache index and event pool (`llm_d_epp_kv_cache_*`), the Disaggregation Sidecar (`llm_d_disagg_sidecar_` and `moriio_dns_` prefixes), and the Coordinator (`llm_d_coordinator_` prefix). To scrape them, see [Collect Metrics](./metrics.md); for ready-to-run queries, see the [PromQL Query Reference](./promql.md), and for alerts built on them, see [Configure Alerts](./alerting.md).

## Request and Latency

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_request_total` | Total request count per model, flow ID, and priority | Baseline for calculating error rate and throughput per model |
| `llm_d_epp_request_error_total` | Error count per model, flow ID, priority, and error code | Rising errors signal backend failures. Alert when error rate exceeds 5% |
| `llm_d_epp_request_duration_seconds` | Response latency distribution per model, flow ID, and priority | The primary latency SLO metric. Tracks full round-trip time from request to response; carries trace exemplars |
| `llm_d_epp_request_size_bytes` | Incoming request size distribution in bytes per model, flow ID, and priority | Helps identify payload size anomalies and exceptionally large incoming prompts |
| `llm_d_epp_response_size_bytes` | Outgoing response size distribution in bytes per model, flow ID, and priority | Tracks outgoing bandwidth usage and response payload size distribution |
| `llm_d_epp_request_input_tokens` | Input token count distribution per model, flow ID, and priority | Helps identify expensive requests. Long prompts cost more compute |
| `llm_d_epp_request_output_tokens` | Output token count distribution per model, flow ID, and priority | Combined with duration, gives normalized cost and generation volume per token |
| `llm_d_epp_request_cached_tokens` | Cached prompt token distribution per model, flow ID, and priority | Measures prompt tokens served from prefix cache as reported by model servers |
| `llm_d_epp_request_running` | Active request count per model, flow ID, and priority | Shows real-time in-flight request concurrency across models |
| `llm_d_epp_request_ntpot_seconds` | Normalized time per output token (NTPOT) distribution per model, flow ID, and priority | Key efficiency metric (lower is better). Compare across pods to find stragglers |
| `llm_d_epp_request_ttft_seconds` | Time to first token (TTFT) distribution per model, flow ID, priority, and streaming mode | Directly measures user-perceived responsiveness and time to initial output byte |
| `llm_d_epp_request_streaming_tpot_seconds` | Time per output token (TPOT) distribution per model, flow ID, and priority; applicable to streaming requests | Tracks ongoing generation speed excluding initial prompt prefill latency |
| `llm_d_epp_request_streaming_itl_seconds` | Inter-token latency (ITL) distribution per model, flow ID, and priority; applicable to streaming requests | Measures pacing between consecutive response body chunks; spikes indicate choppy output |

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
| `llm_d_epp_per_endpoint_nixl_failed_transfers_total` | Per-endpoint failed NIXL KV cache transfers | Surfaces NIXL KV transfer failures between disaggregated prefill and decode pods |
| `llm_d_epp_per_endpoint_nixl_failed_notifications_total` | Per-endpoint failed NIXL KV cache notifications | Surfaces NIXL notification delivery failures between model servers |
| `llm_d_epp_per_endpoint_nixl_kv_expired_requests_total` | Per-endpoint requests whose KV cache expired before read | Indicates disaggregation pipeline delays where cached KV blocks timed out before decode consumed them |

## Scheduler, Plugins & Processing Overhead

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_scheduler_attempts_total` | Scheduling attempt counts by status, target model, endpoint, namespace, and port | Track failed scheduling attempts. High failure rate indicates filter/scorer misconfiguration |
| `llm_d_epp_scheduler_e2e_duration_seconds` | End-to-end scheduling latency distribution | Tracks latency spent within the EPP scheduling cycle |
| `llm_d_epp_plugin_duration_seconds` | Per-plugin processing latency distribution by extension point, plugin type, and name | Identifies slow or bottlenecked scheduling plugins |
| `llm_d_epp_plugin_data_scope_violations_total` | Total endpoint attribute accesses rejected due to undeclared DataKeys | Non-zero value indicates plugin implementation drift from declaration |
| `llm_d_epp_request_processing_duration_seconds` | Time from request receipt until request body has been handled | Measures EPP request ingest and admission overhead |
| `llm_d_epp_response_processing_duration_seconds` | EPP response processing latency | Measures response streaming handler overhead |
| `llm_d_epp_info` | Build information (`commit`, `build_ref`) | Verifies running binary release and build details |
| `llm_d_epp_model_rewrite_decisions_total` | Total model rewrite decisions by rule name, source model, and target model | Tracks request rewrites across configured rewrite rules |

## In-Flight Load

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_inflight_requests` | Requests currently in flight on each endpoint by namespace, producer, flow ID, and priority | Per-replica queue depth for load-aware routing and capacity analysis |
| `llm_d_epp_inflight_tokens` | Tokens currently in flight on each endpoint by namespace, producer, flow ID, and priority | Per-replica token pressure, a finer load signal than request count |

## Disaggregation & Rollout

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_disagg_decision_total` | Routing decisions across disaggregation stages (`decode-only`, `prefill-decode`, `encode-decode`, `encode-prefill-decode`) | Tracks breakdown of requests processed per disaggregation mode |
| `llm_d_epp_disaggregatedset_strict_revision_no_match_total` | Strict revision selections that matched no endpoint | Surfaces requests arriving with a revision header that has no Ready endpoint |
| `llm_d_epp_disaggregatedset_revision_gating_share` | Current weighted revision gating share (0 to 1) per revision and mode | Monitors canary and rolling deployment shares across disaggregated revisions |

## Flow Control

When flow control is enabled (`flowControl` feature gate), these additional metrics are exposed:

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_flow_control_queue_size` | Queued request count per flow ID, priority, pool, and model | Growing queue means the pool cannot keep up. Consider scaling or adjusting priority bands |
| `llm_d_epp_flow_control_queue_bytes` | Queued payload size in bytes per flow ID, priority, pool, and model | Large queued payloads can exhaust EPP memory. Monitor alongside `maxBytes` config |
| `llm_d_epp_flow_control_request_queue_duration_seconds` | Queuing duration distribution per flow ID, priority, outcome, pool, and model | Directly impacts user-perceived latency. High values mean flow control is holding requests too long |
| `llm_d_epp_flow_control_dispatch_cycle_duration_seconds` | Internal dispatch cycle duration distribution | Tracks execution speed and overhead of the flow control scheduler loop |
| `llm_d_epp_flow_control_request_enqueue_duration_seconds` | Request enqueue duration distribution per flow ID, priority, and outcome | Measures admission overhead entering the flow control queue |
| `llm_d_epp_flow_control_pool_saturation` | Pool saturation level (0.0 to 1.0+) by pool and stage (`prefill`, `decode`, `effective`) | Above 1.0 means demand exceeds capacity and flow control is actively throttling. Scale up or shed load |
| `llm_d_epp_flow_control_stale_endpoints` | Number of candidate endpoints with missing or stale telemetry by detector and stage | Non-zero value indicates telemetry scraping issues rather than genuine overload |
| `llm_d_epp_flow_control_detector_saturation` | Saturation reported by each child detector of a `max-saturation-detector` | Identifies which specific detector (e.g., concurrency vs utilization) is driving pool saturation |
| `llm_d_epp_flow_control_capacity_utilization_requests` | Fraction of a priority band's effective request capacity currently occupied (0.0–1.0) | Alert on priority band saturation before requests are rejected (`RejectedCapacity`) |
| `llm_d_epp_flow_control_capacity_utilization_bytes` | Fraction of a priority band's effective byte capacity currently occupied (0.0–1.0) | Monitors memory pressure per priority band to avoid hitting byte limits |
| `llm_d_epp_flow_control_global_capacity_utilization_requests` | Fraction of global request capacity currently occupied across all priority bands (0.0–1.0) | Tracks cluster-wide request saturation against the global capacity ceiling |
| `llm_d_epp_flow_control_global_capacity_utilization_bytes` | Fraction of global byte capacity currently occupied across all priority bands (0.0–1.0) | Tracks cluster-wide memory pressure against the global byte capacity ceiling |
| `llm_d_epp_flow_control_requests_total` | Total requests processed by flow control by outcome | Direct signal for rejection (`outcome="RejectedCapacity"`) and eviction rates |
| `llm_d_epp_flow_control_revocations_issued_total` | Total in-flight eviction revocations issued by priority and pool | Tracks preemptive capacity reclamation events under contention |
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
| `llm_d_epp_prefix_indexer_size` | Entries in the approximate prefix index by plugin | Tracks approximate prefix index memory footprint |
| `llm_d_epp_prefix_indexer_hit_ratio` | Prefix-match hit ratio distribution by plugin | High hit ratio indicates effective approximate prefix caching |
| `llm_d_epp_prefix_indexer_hit_bytes` | Bytes matched per prefix lookup by plugin | Quantifies data reused from approximate prefix cache |
| `llm_d_epp_prefix_predicted_cached_tokens` | Prompt tokens predicted to hit the chosen endpoint's prefix cache | Compares predicted cache reuse against delivered tokens to evaluate routing accuracy |
| `llm_d_epp_prefix_best_predicted_cached_tokens` | Highest prefix prediction among endpoints the scheduler selected from | Locates shortfall in scoring and picking against reachable reuse |
| `llm_d_epp_prefix_best_available_cached_tokens` | Highest prefix prediction among candidate endpoints before filtering | Identifies cache reuse lost due to load shedding or filtering |
| `llm_d_epp_prefix_prompt_tokens` | Prompt tokens the prefix predictions were measured against | Normalizes prefix cache predictions to compute predicted hit ratios |
| `llm_d_epp_prefix_mm_predicted_cached_tokens` | Multimodal prompt tokens predicted to hit the chosen endpoint's prefix cache | Evaluates multimodal prefix cache prediction efficiency |
| `llm_d_epp_prefix_mm_prompt_tokens` | Multimodal prompt tokens the multimodal prediction was measured against | Normalizes multimodal prefix cache predictions to compute multimodal hit ratios |
| `llm_d_epp_token_producer_render_duration_seconds` | Duration of token-producer render calls by backend and result | Tracks latency of prompt tokenization and rendering via vLLM backend |
| `llm_d_epp_token_producer_render_failures_total` | Total failed token-producer render calls by backend and reason | Surfaces render timeouts, connection errors, and decoding failures |
| `llm_d_epp_encoder_cache_queries_total` | Total multimodal encoder-cache lookups by plugin and modality | Volume of multimodal items queried against cache |
| `llm_d_epp_encoder_cache_hits_total` | Encoder-cache hits per pod and modality | Tracks multimodal embedding cache reuse |
| `llm_d_epp_encoder_cache_hit_ratio` | Hit ratio distribution for encoder-cache lookups | Surfaces uneven multimodal cache locality across pods |
| `llm_d_epp_prefix_cache_affinity_filter_decisions_total` | Prefix cache affinity filter decisions by outcome | Surfaces whether scheduling decisions were guided by cache affinity or overridden by load gates |

## KV-Cache Index & Events (Precise Prefix Cache)

These metrics belong to the precise-prefix-cache pipeline (enabled via `indexerConfig.kvBlockIndexConfig.enableMetrics`):

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_kv_cache_index_admissions_total` | Blocks admitted to the KV-cache index | Tracks index ingestion volume of new KV blocks |
| `llm_d_epp_kv_cache_index_evictions_total` | Blocks evicted from the KV-cache index | Monitors index turnover and capacity pressure |
| `llm_d_epp_kv_cache_index_lookup_requests_total` | Total KV-cache index lookups performed | Tracks query throughput into the precise prefix cache index |
| `llm_d_epp_kv_cache_index_lookup_hits_total` | Contiguous prefix blocks matched by the best pod per lookup | Quantifies cache block reuse across routing decisions |
| `llm_d_epp_kv_cache_index_max_pod_hit_count_total` | Longest contiguous per-pod prefix chain observed per lookup | Identifies maximum prefix match depth available in the cluster |
| `llm_d_epp_kv_cache_index_lookup_latency_seconds` | KV-cache index lookup and walk latency distribution | Measures latency overhead of prefix tree lookups during request scheduling |
| `llm_d_epp_kv_cache_events_dedup_removed_hashes_suppressed_total` | Block removal hashes suppressed by deduplication | Verifies dedup filter efficiency in suppressing redundant eviction events |
| `llm_d_epp_kv_cache_events_dedup_removed_hashes_forwarded_total` | Block removal hashes forwarded for eviction | Tracks actual eviction event volume processed by the index |
| `llm_d_epp_kv_cache_events_stores_skipped_total` | KV-cache store events skipped before prefix indexing by cache kind and reason | Surfaces skipped store events by cache kind and reason |
| `llm_d_epp_kv_cache_events_removals_skipped_total` | KV-cache removal events skipped before prefix indexing by cache kind and reason | Surfaces skipped removal events for untracked cache groups |
| `llm_d_epp_kv_cache_events_active_subscribers` | Number of ZeroMQ subscribers currently managed | Confirms EPP connection health to model-server KV event streams |
| `llm_d_epp_kv_cache_events_subscriber_reconnections_total` | ZeroMQ subscriber reconnection attempts per pod | High count indicates network instability or crashing model-server pods |
| `llm_d_epp_kv_cache_events_messages_received_total` | Messages received from ZeroMQ subscribers per pod | Measures KV event message volume streamed from each model server |
| `llm_d_epp_kv_cache_events_zmq_errors_total` | ZeroMQ subscriber errors by pod and operation | Alerts on communication failures with model-server event publishers |
| `llm_d_epp_kv_cache_events_pool_queue_depth` | Total messages queued across all event worker shards | High queue depth indicates event processing backlog in the EPP |
| `llm_d_epp_kv_cache_events_pool_capacity` | Number of worker shards in the event processing pool | Confirms event worker pool sizing and concurrency |

## ThunderAgent Session Affinity

These metrics belong to the `thunderagent` session affinity plugin:

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_thunder_agent_sessions` | Tracked agent sessions by state (`running`, `idle`) | Monitors active and idle session concurrency under session affinity routing |
| `llm_d_epp_thunder_agent_endpoint_working_set_tokens` | Session KV working set per endpoint in tokens | Compare with engine KV cache utilization: high working set over low utilization reveals KV cache thrashing |
| `llm_d_epp_thunder_agent_endpoint_capacity_tokens` | Total KV token capacity per endpoint | Baselines session capacity limits per backend replica |

## Predicted Latency & SLO

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_request_predicted_ttft_seconds` | Predicted TTFT distribution | Used by latency-based scheduling to evaluate candidates |
| `llm_d_epp_request_ttft_prediction_duration_seconds` | Time spent computing TTFT predictions | Measures overhead of the predictor sidecar |
| `llm_d_epp_request_predicted_tpot_seconds` | Predicted TPOT distribution | Evaluates token generation latency across backends |
| `llm_d_epp_request_tpot_prediction_duration_seconds` | Time spent computing TPOT predictions | Measures TPOT predictor execution latency |
| `llm_d_epp_request_slo_violation_total` | Count of requests violating configured latency SLOs | Primary alert signal for SLO degradation |
| `llm_d_epp_inference_request_metric` | Consolidated gauge for observed request metrics and SLO states | Flexible single-series export of TTFT, TPOT, and SLO metrics |
| `llm_d_epp_request_prediction_failures_total` | Failed latency prediction attempts by reason | Surfaces predictor sidecar communication and inference failures |

## ext_proc Streams & Data Layer Errors

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_epp_extproc_streams_inflight` | Number of open ext_proc gRPC streams (opt-in) | Monitors Envoy↔EPP gRPC stream connections |
| `llm_d_epp_extproc_stream_duration_seconds` | Duration ext_proc gRPC streams stay open | Sudden short durations indicate stream reconnect churn |
| `llm_d_epp_extproc_streams_total` | Completed ext_proc gRPC streams by status code | Surfaces abnormal stream disconnects and errors |
| `llm_d_epp_datalayer_poll_errors_total` | Data-source poll failures per source type | Signals failing telemetry scrapes from model servers |
| `llm_d_epp_datalayer_extract_errors_total` | Extractor failures per source/extractor type | Signals parsing failures on scraped telemetry payloads |

## Disaggregation Sidecar & MoRI-IO DNS

These metrics are emitted by the disaggregation sidecar proxy and MoRI-IO peer resolver:

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_disagg_sidecar_requests_total` | Total intercepted inference API requests by API type | Monitors request traffic entering the disaggregation sidecar proxy |
| `llm_d_disagg_sidecar_disagg_requests_total` | Total requests routed through disaggregation by disaggregation type | Tracks disaggregation execution volume through the sidecar |
| `llm_d_disagg_sidecar_encode_duration_seconds` | Encode stage latency distribution in seconds | Measures latency spent in multimodal encoding |
| `llm_d_disagg_sidecar_prefill_duration_seconds` | Prefill stage latency distribution in seconds | Measures time spent in the disaggregated prefill stage |
| `llm_d_disagg_sidecar_decode_duration_seconds` | Decode stage latency distribution in seconds | Measures generation latency in the disaggregated decode stage |
| `llm_d_disagg_sidecar_request_errors_total` | Disaggregation stage errors by stage (`encode`, `prefill`, `decode`) | Identifies stage-specific failures in disaggregated request pipelines |
| `moriio_dns_reresolve_total` | Successful request-path re-resolutions of peer DNS names | Confirms MoRI-IO peer address re-resolution activity |
| `moriio_dns_ip_changed_total` | Re-resolutions where a peer resolved to a new IP | Indicates peer pod restarts or IP reassignments |
| `moriio_dns_lookup_failures_total` | Failed peer DNS lookups on the request path | Alerts on DNS resolution failures where sidecar falls back to last-known-good IP |

## Coordinator Metrics

These metrics are emitted by the llm-d Coordinator (`llm_d_coordinator_` prefix) at entry and across pipeline execution:

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `llm_d_coordinator_request_total` | Total inbound client requests accepted by the coordinator by model | Baseline throughput for client-facing inference requests including malformed bodies |
| `llm_d_coordinator_request_error_total` | Failed client requests by model and error code | Primary failure signal for the coordinator pipeline |
| `llm_d_coordinator_request_duration_seconds` | End-to-end request latency distribution by model | Full round-trip time from client request to final response byte |
| `llm_d_coordinator_request_size_bytes` | Incoming request body size distribution in bytes | Tracks inbound payload sizes before pipeline fan-out |
| `llm_d_coordinator_response_size_bytes` | Total bytes written to the client per request by stream mode | Tracks egress data volume including partial writes on client disconnect |
| `llm_d_coordinator_request_input_tokens` | Prompt token count distribution measured after rendering | Measures input prompt size distribution across client requests |
| `llm_d_coordinator_request_running` | Active client requests currently being processed by the coordinator | Tracks real-time coordinator concurrency |
| `llm_d_coordinator_step_duration_seconds` | Per-step wall-time latency distribution across pipeline stages | Pinpoints which pipeline stage dominates request duration |
| `llm_d_coordinator_step_errors_total` | Total pipeline-step failures by step and error code | Identifies which internal stage failed during request execution |
| `llm_d_coordinator_step_running` | Requests currently executing each pipeline step | Tracks concurrency per pipeline stage (decode reflects active streaming responses) |
| `llm_d_coordinator_upstream_request_total` | Outbound calls to upstream services by upstream target | Measures external call volume and fan-out (e.g. concurrent encode subrequests) |
| `llm_d_coordinator_upstream_request_duration_seconds` | Latency distribution of individual outbound calls to upstream services | Measures per-call latency of upstream services independent of batch fan-out |
| `llm_d_coordinator_execution_path_total` | Client requests by executed disaggregation path | Tracks real-world execution path distribution across workloads |
| `llm_d_coordinator_conditional_decode_probes_total` | Conditional-decode probes by outcome (`served`, `deferred`, `error`, `transport_error`) | Evaluates cache hit rate and early-exit efficiency of conditional decode |
| `llm_d_coordinator_encode_subrequests` | Number of encode subrequests generated per client request | Quantifies multimodal fan-out across images |
| `llm_d_coordinator_orchestration_overhead_seconds` | Coordinator wall time outside parsing and step execution | Measures internal coordination overhead of the pipeline executor |
| `llm_d_coordinator_media_items` | Media items found per client request by media type (`image`, `other`) | Measures multimodal input richness per request |
| `llm_d_coordinator_media_download_duration_seconds` | Duration of outbound media download attempts by result | Measures latency and reliability of external media asset downloads |
