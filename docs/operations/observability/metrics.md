# Metrics

This page covers how to enable and interpret metrics from an llm-d deployment. For Prometheus and Grafana installation, see [Observability Setup](./setup.md) first.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
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
| `sglang:num_running_reqs` | Active requests being processed | High values indicate GPU saturation; new requests will queue |
| `sglang:num_queue_reqs` | Requests queued, waiting to be processed | Non-zero means pods are saturated. Primary signal for autoscaling decisions |
| `sglang:token_usage` | KV cache token utilization (0.0 to 1.0) | Above 0.9 means GPU memory is nearly full |
| `sglang:time_to_first_token_seconds` (histogram) | Time from request arrival to first generated token (TTFT) | Directly impacts user experience. Use `histogram_quantile()` to query percentiles |
| `sglang:inter_token_latency_seconds` (histogram) | Time between consecutive generated tokens (ITL) | Affects streaming response speed. Use `histogram_quantile()` to query percentiles |
| `sglang:prompt_tokens_total` | Total input tokens processed | Use `rate()` to get tokens/sec per pod |
| `sglang:generation_tokens_total` | Total output tokens generated | Use `rate()` alongside prompt tokens to get total throughput |

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
| `llm_d_epp_ready_endpoints` | Number of ready endpoints in the pool | If this drops below expected count, pods are crashing or not scheduling |
| `llm_d_epp_scheduler_attempts_total` | Scheduling attempt counts and outcomes | Track failed scheduling attempts. High failure rate indicates filter/scorer misconfiguration |

### Flow Control Metrics

When flow control is enabled, these additional metrics are exposed:

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `llm_d_epp_flow_control_queue_size` | Queued request count per flow ID and priority | Growing queue means the pool cannot keep up. Consider scaling or adjusting priority bands |
| `llm_d_epp_flow_control_queue_bytes` | Queued payload size in bytes per flow ID and priority | Large queued payloads can exhaust EPP memory. Monitor alongside `maxBytes` config |
| `llm_d_epp_flow_control_request_queue_duration_seconds` | Queuing duration distribution per flow ID and priority | Directly impacts user-perceived latency. High values mean flow control is holding requests too long |
| `llm_d_epp_flow_control_dispatch_cycle_duration_seconds` | Internal dispatch cycle duration distribution | Tracks execution speed of the flow control scheduler loop |
| `llm_d_epp_flow_control_request_enqueue_duration_seconds` | Request enqueue duration distribution per flow ID and priority | Measures admission overhead entering the flow control queue |
| `llm_d_epp_flow_control_pool_saturation` | Pool saturation level (0.0 to 1.0+) | Above 1.0 means demand exceeds capacity and flow control is actively throttling. Scale up or shed load |

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
