# Metrics

This page covers how to enable and interpret metrics from an llm-d deployment. For Prometheus and Grafana installation, see [Observability Setup](./setup.md) first.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Prerequisites

- A running llm-d deployment with an InferencePool and model servers — see the [quickstart](../../getting-started/quickstart.md) if needed
- Prometheus and Grafana installed — see [Observability Setup](./setup.md)

## Step 1: Enable Model Server Metrics

Model server metrics are enabled by default. Configuration varies by deployment method.

### Kustomize Deployments

If you deployed your model server using `kustomize build`, add the monitoring component to your `kustomization.yaml`:

```yaml
components:
  - ../../../recipes/modelserver/components/monitoring       # decode PodMonitor
  # - ../../../recipes/modelserver/components/monitoring-pd  # add for prefill/decode disaggregation
```

The monitoring component creates PodMonitors that scrape model server metrics. See [`guides/recipes/modelserver/components/monitoring/`](../../../guides/recipes/modelserver/components/monitoring/) for details.

### Verify PodMonitors

Verify the PodMonitors exist:

```bash
kubectl get podmonitors -n ${NAMESPACE}
```

Expected output:

```text
NAME                    AGE
decode-podmonitor       5m
prefill-podmonitor      5m
```

### Key vLLM Metrics

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `vllm:num_requests_running` | Active requests being processed | High values indicate GPU saturation; new requests will queue. Watch for sustained spikes |
| `vllm:num_requests_waiting` | Requests queued, waiting to be processed | Non-zero means pods are saturated. Primary signal for autoscaling decisions |
| `vllm:kv_cache_usage_perc` | KV cache utilization (0.0 to 1.0) | Above 0.9 means GPU memory is nearly full and requests may get preempted or rejected |
| `vllm:time_to_first_token_seconds` (histogram) | Time from request arrival to first generated token (TTFT) | Directly impacts user experience. Use `histogram_quantile()` to query percentiles |
| `vllm:inter_token_latency_seconds` (histogram) | Time between consecutive generated tokens (ITL) | Affects streaming response speed. High ITL causes choppy output. Use `histogram_quantile()` to query percentiles |
| `vllm:prefix_cache_hits_total` | Number of prefix cache hits | Compare with `prefix_cache_queries_total` to get hit rate. Low hit rate suggests the EPP is not routing effectively |
| `vllm:prefix_cache_queries_total` | Total prefix cache lookups | Divide `prefix_cache_hits_total` by this to get hit rate. A dropping ratio indicates routing or prompt pattern changes |
| `vllm:prompt_tokens_total` | Total input tokens processed | Use `rate()` to get tokens/sec per pod. Compare across pods to spot uneven load distribution |
| `vllm:generation_tokens_total` | Total output tokens generated | Use `rate()` alongside prompt tokens to get total throughput. A drop signals degraded model performance |

### Key SGLang Metrics

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `sglang_num_running_reqs` | Active requests being processed | High values indicate GPU saturation; new requests will queue |
| `sglang_num_queue_reqs` | Requests queued, waiting to be processed | Non-zero means pods are saturated. Primary signal for autoscaling decisions |
| `sglang_token_usage` | KV cache token utilization (0.0 to 1.0) | Above 0.9 means GPU memory is nearly full |
| `sglang_cache_hit_rate` | Prefix cache hit rate (0.0 to 1.0) | High hit rate indicates efficient KV cache reuse |
| `sglang_time_to_first_token_seconds` (histogram) | Time from request arrival to first generated token (TTFT) | Directly impacts user experience. Use `histogram_quantile()` to query percentiles |
| `sglang_inter_token_latency_seconds` (histogram) | Time between consecutive generated tokens (ITL) | Affects streaming response speed. Use `histogram_quantile()` to query percentiles |
| `sglang_prompt_tokens_total` | Total input tokens processed | Use `rate()` to get tokens/sec per pod |
| `sglang_generation_tokens_total` | Total output tokens generated | Use `rate()` alongside prompt tokens to get total throughput |

## Step 3: Enable EPP Metrics

EPP (Endpoint Picker) metrics are enabled by default. To verify or enable manually, see the [Monitoring & Tracing Configuration](https://github.com/llm-d/llm-d-router/tree/main/config/charts#4-monitoring--tracing-configuration) section in the llm-d-router Helm chart docs.

Verify the ServiceMonitor exists:

```bash
kubectl get servicemonitors -n ${NAMESPACE}
```

Expected output:

```text
NAME                    AGE
epp-servicemonitor      5m
```

### Key llm-d Router EPP Metrics

The EPP exposes Prometheus metrics under the `llm_d_epp_` prefix.

#### Request and Latency

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
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

#### Inference Pool

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_ready_endpoints` | Number of ready endpoints in the pool | If this drops below expected count, pods are crashing or not scheduling |
| `llm_d_epp_average_kv_cache_utilization` | Mean KV cache utilization across the pool | High average utilization signals the pool is nearing memory capacity |
| `llm_d_epp_average_queue_size` | Mean queue depth across model servers | Primary signal for pool saturation and autoscaling decisions |
| `llm_d_epp_average_running_requests` | Mean running requests across model servers | Tracks concurrency across endpoints |
| `llm_d_epp_std_dev_kv_cache_utilization` | Spread of KV cache utilization across endpoints | High standard deviation indicates uneven cache distribution |
| `llm_d_epp_std_dev_queue_size` | Spread of queue depth across endpoints | High variance points to routing imbalances |
| `llm_d_epp_std_dev_running_requests` | Spread of in-flight requests across endpoints | Helps diagnose stragglers or hotspotting |
| `llm_d_epp_per_endpoint_queue_size` | Per-endpoint queue depth | Identifies overloaded model-server replicas |

#### Scheduler, Plugins & Processing Overhead

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_scheduler_attempts_total` | Scheduling attempt counts and outcomes | Track failed scheduling attempts. High failure rate indicates filter/scorer misconfiguration |
| `llm_d_epp_scheduler_e2e_duration_seconds` | End-to-end scheduling latency distribution | Tracks latency spent within the EPP scheduling cycle |
| `llm_d_epp_plugin_duration_seconds` | Per-plugin processing latency distribution | Identifies slow or bottlenecked scheduling plugins |
| `llm_d_epp_plugin_data_scope_violations_total` | Total endpoint attribute accesses rejected due to undeclared DataKeys | Non-zero value indicates plugin implementation drift from declaration |
| `llm_d_epp_request_processing_duration_seconds` | Time from request receipt until request body has been handled | Measures EPP request ingest and admission overhead |
| `llm_d_epp_response_processing_duration_seconds` | EPP response processing latency | Measures response streaming handler overhead |
| `llm_d_epp_info` | Build information (`commit`, `build_ref`) | Verifies running binary release and build details |
| `llm_d_epp_model_rewrite_decisions_total` | Total model rewrite decisions | Tracks request rewrites across configured rewrite rules |

#### In-Flight Load

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_inflight_requests` | Requests currently in flight on each endpoint | Per-replica queue depth for load-aware routing and capacity analysis |
| `llm_d_epp_inflight_tokens` | Tokens currently in flight on each endpoint | Per-replica token pressure, a finer load signal than request count |

#### Disaggregation & Rollout

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_disagg_decision_total` | Routing decisions across disaggregation stages | Tracks breakdown of decode-only, prefill-decode, encode-decode, and full pipeline decisions |
| `llm_d_epp_pd_decision_total` | Prefill/decode disaggregation decisions (deprecated handler) | Backward-compatible counter for P/D routing decisions |
| `llm_d_epp_disaggregatedset_strict_header_no_match_total` | Strict header selections that matched no endpoint | Surfaces strict selector misconfigurations that fail closed |
| `llm_d_epp_disaggregatedset_revision_gating_share` | Current weighted revision gating share (0 to 1) | Monitors canary and rolling deployment shares across disaggregated revisions |

#### Flow Control Metrics

When flow control is enabled (`flowControl` feature gate), these additional metrics are exposed:

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
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

#### Prefix & Multimodal Cache

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_prefix_indexer_size` | Entries in the approximate prefix index | Tracks index memory footprint |
| `llm_d_epp_prefix_indexer_hit_ratio` | Prefix-match hit ratio distribution | High hit ratio indicates effective prefix caching |
| `llm_d_epp_prefix_indexer_hit_bytes` | Bytes matched per prefix lookup | Quantifies data reused from cache |
| `llm_d_epp_encoder_cache_queries_total` | Total multimodal encoder-cache lookups | Volume of multimodal items queried against cache |
| `llm_d_epp_encoder_cache_hits_total` | Encoder-cache hits per pod | Tracks multimodal embedding cache reuse |
| `llm_d_epp_encoder_cache_hit_ratio` | Hit ratio distribution for encoder-cache lookups | Surfaces uneven multimodal cache locality across pods |

#### Predicted Latency & SLO

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_request_predicted_ttft_seconds` | Predicted TTFT distribution | Used by latency-based scheduling to evaluate candidates |
| `llm_d_epp_request_ttft_prediction_duration_seconds` | Time spent computing TTFT predictions | Measures overhead of the predictor sidecar |
| `llm_d_epp_request_predicted_tpot_seconds` | Predicted TPOT distribution | Evaluates token generation latency across backends |
| `llm_d_epp_request_tpot_prediction_duration_seconds` | Time spent computing TPOT predictions | Measures TPOT predictor execution latency |
| `llm_d_epp_request_slo_violation_total` | Count of requests violating configured latency SLOs | Primary alert signal for SLO degradation |
| `llm_d_epp_inference_request_metric` | Consolidated gauge for observed request metrics and SLO states | Flexible single-series export of TTFT, TPOT, and SLO metrics |

#### ext_proc Streams & Data Layer Errors

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_extproc_streams_inflight` | Number of open ext_proc gRPC streams (opt-in) | Monitors Envoy↔EPP gRPC stream connections |
| `llm_d_epp_extproc_stream_duration_seconds` | Duration ext_proc gRPC streams stay open | Sudden short durations indicate stream reconnect churn |
| `llm_d_epp_extproc_streams_total` | Completed ext_proc gRPC streams by status code | Surfaces abnormal stream disconnects and errors |
| `llm_d_epp_datalayer_poll_errors_total` | Data-source poll failures per source type | Signals failing telemetry scrapes from model servers |
| `llm_d_epp_datalayer_extract_errors_total` | Extractor failures per source/extractor type | Signals parsing failures on scraped telemetry payloads |

### Key Batch Gateway Metrics

Only relevant if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md). These are the operationally useful series; the definitional source of truth for names, types, and labels is [`docs/guides/metrics.md`](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/metrics.md) in the component repo, which also documents the token, cancellation, and startup-recovery counters not listed here.

Unlike the EPP and vLLM metrics above, these names carry no `llm_d_` prefix, so scope queries by `namespace` to avoid picking up unrelated workloads.

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `http_requests_total` | API server requests by method, path, and status | Baseline for API-side error rate — batch submission failures show up here, not in job metrics |
| `http_request_duration_seconds` | API server request latency distribution | Submission and status-poll responsiveness |
| `jobs_processed_total` | Jobs processed by `result` (`success`, `failed`, `skipped`, `re_enqueued`, `expired`) and `reason` | The primary outcome signal. `expired` means jobs aged out before running, which is a capacity problem rather than a failure |
| `job_queue_wait_duration_seconds` | Time in the priority queue before pickup | Leading indicator of backlog; rises before end-to-end latency does |
| `job_processing_duration_seconds` | End-to-end processing duration by `size_bucket` | Separates "large jobs are slow" from "everything is slow" |
| `batch_job_e2e_latency_seconds` | Submission to terminal state, by `status` | What the user actually waits for, including queue time |
| `active_workers` / `total_workers` | Active vs configured worker pool size | Their ratio is the saturation signal — sustained near 1.0 means the pool is the bottleneck |
| `processor_inflight_requests` | In-flight inference requests during execution | Load the batch path is placing on the inference backends |
| `batch_reconciler_errors_total` | GC reconciler cycle errors | Non-zero means orphaned jobs are not being recovered |
| `batch_reconciler_orphans_recovered_total` | Orphans recovered by `action` | Sustained non-zero points at processor crashes upstream |
| `file_storage_operations_total` | File storage operations by `operation`, `component`, and `status` | `status="exhausted"` means retries gave up — input or output data is inaccessible |

> [!NOTE]
> The Batch Gateway dashboards compute histogram quantiles by summing buckets across pods before applying `histogram_quantile`, which yields a fleet-wide approximate percentile rather than an exact one. This is acceptable because each job is processed by exactly one pod, but it can mask a single consistently-slow pod. Add `by (le, pod)` for a per-pod breakdown.

For alerts built on these metrics, see [Alerting](./alerting.md#batch-gateway-batch-gatewayrules).

## Step 4: View Dashboards

llm-d provides pre-built Grafana dashboards for common monitoring scenarios.

### Access Grafana

> [!NOTE]
> The commands below use namespace and service names from the bundled install script. If you use an existing Prometheus or Grafana instance, adjust the namespace and service names accordingly.

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80
# Open http://localhost:3000
# Default login: admin / admin
```

### Import Dashboards

Load all llm-d dashboards into Grafana:

```bash
./guides/recipes/observability/load-llm-d-dashboards.sh
```

Verify dashboards were imported:

```bash
kubectl get configmaps -n llm-d-monitoring -l grafana_dashboard=1
```

Expected output:

```text
NAME                                              DATA   AGE
llm-d-vllm-overview                               1      30s
llm-d-sglang-overview                             1      30s
llm-d-failure-saturation-dashboard                1      30s
llm-d-diagnostic-drilldown-dashboard              1      30s
llm-d-performance-kv-cache                        1      30s
llm-d-pd-coordinator-metrics                      1      30s
llm-d-batch-gateway-apiserver                     1      30s
llm-d-batch-gateway-processor                     1      30s
llm-d-batch-gateway-gc                            1      30s
```

Or import individual dashboard JSON files manually from `guides/recipes/observability/grafana/dashboards/`:

| Dashboard | What it shows |
|-----------|--------------|
| `llm-d-vllm-overview.json` | General vLLM metrics overview |
| `llm-d-sglang-overview.json` | General SGLang metrics overview |
| `llm-d-failure-saturation-dashboard.json` | Failure and saturation indicators |
| `llm-d-diagnostic-drilldown-dashboard.json` | Detailed diagnostic metrics for troubleshooting |
| `llm-d-performance-kv-cache.json` | Performance metrics including KV cache utilization |
| `llm-d-pd-coordinator-metrics.json` | Prefill/decode disaggregation metrics |
| `llm-d-batch-gateway-apiserver.json` | Batch Gateway API server request rate, latency, and in-flight requests |
| `llm-d-batch-gateway-processor.json` | Batch Gateway job throughput, queue wait, worker saturation, and token usage |
| `llm-d-batch-gateway-gc.json` | Batch Gateway GC reconciler cycles, orphan recovery, and errors |

The three Batch Gateway dashboards are only useful if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md); each has a `namespace` variable to select the namespace it runs in.

## Step 5: Query Metrics

Access the Prometheus UI:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090
# Open http://localhost:9090 (or https://localhost:9090 if TLS is enabled)
```

## Cleanup

```bash
./guides/recipes/observability/install-prometheus-grafana.sh -u -n llm-d-monitoring
```

## Troubleshooting

### Autoscaler reports "http: server gave HTTP response to HTTPS client"

The autoscaler is configured for HTTPS but Prometheus is serving HTTP. Enable TLS:

```bash
./guides/recipes/observability/install-prometheus-grafana.sh -u
./guides/recipes/observability/install-prometheus-grafana.sh --enable-tls
```

### Metrics not appearing in Prometheus

1. Check that PodMonitors and ServiceMonitors exist:

   ```bash
   kubectl get podmonitors,servicemonitors -n ${NAMESPACE}
   ```

2. Verify Prometheus is scraping the targets. Open `http://localhost:9090/targets` (after port-forwarding) and check that vLLM and EPP targets show `UP`

3. Confirm pods expose metrics:

   ```bash
   VLLM_POD=$(kubectl get pods -n ${NAMESPACE} -l app=my-model -o jsonpath='{.items[0].metadata.name}')
   kubectl port-forward -n ${NAMESPACE} ${VLLM_POD} 8000:8000
   curl http://localhost:8000/metrics | head -20
   ```

### Grafana dashboards show "No data"

1. Verify the Grafana datasource points to the correct Prometheus URL
2. Check that metrics are flowing in Prometheus first (use the Prometheus UI)
3. If using TLS, ensure the Grafana datasource is configured for HTTPS with the correct CA certificate
