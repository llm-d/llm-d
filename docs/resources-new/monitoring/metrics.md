# Metrics

This guide shows how to collect and visualize metrics from an llm-d deployment using Prometheus and Grafana.

> [!NOTE]
> This guide assumes you have a running llm-d deployment with an InferencePool and model servers. See the [quickstart](../../getting-started/quickstart.md) if you need to set one up first.

## Prerequisites

- A running llm-d basic stack (llm-d Router + model servers)
- [Helm](https://helm.sh/docs/intro/install/) (for llm-d Router charts and optional Prometheus install)
- A Prometheus instance accessible to the cluster (see [Step 1](#step-1-install-prometheus-and-grafana) if you don't have one)

> [!NOTE]
> Commands in this guide use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Step 1: Install Prometheus and Grafana

If you already have Prometheus running in your cluster, skip to [Step 2](#step-2-enable-vllm-metrics).

> [!NOTE]
> llm-d provides an install script that deploys Prometheus and Grafana with sensible defaults. For production environments, see the platform-specific notes below.

```bash
# Install Prometheus + Grafana into the llm-d-monitoring namespace
./docs/monitoring/scripts/install-prometheus-grafana.sh
```

For HTTPS/TLS (required by autoscalers like WVA):

```bash
./docs/monitoring/scripts/install-prometheus-grafana.sh --enable-tls
```

Verify the installation:

```bash
kubectl get pods -n llm-d-monitoring
```

Expected output:

```text
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-llmd-kube-prometheus-stack-alertmanager-0    2/2     Running   0          30s
llmd-grafana-xxxxxxxxx-xxxxx                             3/3     Running   0          30s
prometheus-llmd-kube-prometheus-stack-prometheus-0        2/2     Running   0          30s
```

### Platform-Specific Configuration

#### OpenShift

OpenShift provides a built-in Prometheus stack via User Workload Monitoring. Enable it instead of installing a separate Prometheus:

- See the [OpenShift monitoring documentation](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index) to enable User Workload Monitoring
- Prometheus endpoint: `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`

#### GKE

**Option 1 — Google Managed Prometheus (recommended)**

GKE clusters include [Google Managed Prometheus (GMP)](https://cloud.google.com/stackdriver/docs/managed-prometheus) by default. To use GMP as a Grafana data source, follow the [GMP Grafana integration guide](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query#ui-grafana).

**Option 2 — In-cluster Prometheus**

If you need direct HTTP API access or prefer a standalone instance:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n llm-d-monitoring --create-namespace
```

Verify the in-cluster Prometheus is running:

```bash
kubectl get pods -n llm-d-monitoring -l app.kubernetes.io/name=prometheus
```

Expected output:

```text
NAME                                                 READY   STATUS    RESTARTS   AGE
prometheus-kube-prometheus-stack-prometheus-0         2/2     Running   0          60s
```

- Enable [automatic application monitoring](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) for vLLM metric collection
- GKE also provides a built-in [inference gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard)

## Step 2: Enable vLLM Metrics

vLLM metrics are enabled by default. Configuration varies by deployment method.

### Kustomize Deployments

If you deployed your model server using `kustomize build`, add the monitoring component to your `kustomization.yaml`:

```yaml
components:
  - ../../../recipes/modelserver/components/monitoring       # decode PodMonitor
  # - ../../../recipes/modelserver/components/monitoring-pd  # add for prefill/decode disaggregation
```

The monitoring component creates PodMonitors that scrape vLLM metrics. See [`guides/recipes/modelserver/components/monitoring/`](../../../guides/recipes/modelserver/components/monitoring/) for details.

### Helm Deployments

If you deployed using Helm (`ms-*/values.yaml`), enable PodMonitors in your values:

```yaml
# In your ms-*/values.yaml
decode:
  monitoring:
    podmonitor:
      enabled: true

prefill:
  monitoring:
    podmonitor:
      enabled: true
```

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

## Step 3: Enable EPP Metrics

EPP (Endpoint Picker) metrics are enabled by default. To verify or enable manually:

```yaml
# In your gaie-*/values.yaml
inferenceExtension:
  monitoring:
    prometheus:
      enabled: true
```

Verify the ServiceMonitor exists:

```bash
kubectl get servicemonitors -n ${NAMESPACE}
```

Expected output:

```text
NAME                    AGE
epp-servicemonitor      5m
```

### Key EPP Metrics

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `inference_objective_request_total` | Total request count per model | Baseline for calculating error rate and throughput per model |
| `inference_objective_request_error_total` | Total error count per model | Rising errors signal backend failures. Alert when error rate exceeds 5% |
| `inference_objective_request_duration_seconds` | End-to-end response latency | The SLO metric. Tracks full round-trip time from request to response |
| `inference_objective_input_tokens` | Input token count per request | Helps identify expensive requests. Long prompts cost more compute |
| `inference_objective_output_tokens` | Output token count per request | Combined with duration, gives normalized cost per token |
| `inference_objective_normalized_time_per_output_token_seconds` | Normalized time per output token (NTPOT) | Key efficiency metric (lower is better). Compare across pods to find stragglers |
| `inference_objective_running_requests` | Currently active requests per model | Shows real-time load distribution. Uneven distribution suggests the EPP may need tuning |
| `inference_pool_average_kv_cache_utilization` | Average KV cache utilization across the pool | Pool-wide memory pressure indicator. Above 0.8, consider scaling up to avoid preemptions |
| `inference_pool_average_queue_size` | Average queue depth across the pool | Pool-wide saturation signal. Non-zero means requests are waiting |
| `inference_pool_ready_pods` | Number of ready pods in the pool | If this drops below expected count, pods are crashing or not scheduling |
| `inference_extension_scheduler_attempts_total` | Scheduling attempt counts and outcomes | Track failed scheduling attempts. High failure rate indicates filter/scorer misconfiguration |

### Flow Control Metrics

When flow control is enabled, these additional metrics are exposed:

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `inference_extension_flow_control_queue_size` | Requests currently queued | Growing queue means the pool cannot keep up. Consider scaling or adjusting priority bands |
| `inference_extension_flow_control_queue_bytes` | Total size of queued requests in bytes | Large queued payloads can exhaust EPP memory. Monitor alongside `maxBytes` config |
| `inference_extension_flow_control_request_queue_duration_seconds` | Time a request spends in the queue | Directly impacts user-perceived latency. High values mean flow control is holding requests too long |
| `inference_extension_flow_control_pool_saturation` | Pool saturation level (0.0 to 1.0+) | Above 1.0 means demand exceeds capacity and flow control is actively throttling. Scale up or shed load |

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
./docs/monitoring/scripts/load-llm-d-dashboards.sh
```

Verify dashboards were imported:

```bash
kubectl get configmaps -n llm-d-monitoring -l grafana_dashboard=1
```

Expected output:

```text
NAME                                              DATA   AGE
llmd-llm-d-vllm-overview                          1      30s
llmd-llm-d-failure-saturation-dashboard           1      30s
llmd-llm-d-diagnostic-drilldown-dashboard         1      30s
llmd-llm-performance-kv-cache                     1      30s
llmd-pd-coordinator-metrics                       1      30s
```

Or import individual dashboard JSON files manually from `docs/monitoring/grafana/dashboards/`:

| Dashboard | What it shows |
|-----------|--------------|
| `llm-d-vllm-overview.json` | General vLLM metrics overview |
| `llm-d-failure-saturation-dashboard.json` | Failure and saturation indicators |
| `llm-d-diagnostic-drilldown-dashboard.json` | Detailed diagnostic metrics for troubleshooting |
| `llm-performance-kv-cache.json` | Performance metrics including KV cache utilization |
| `pd-coordinator-metrics.json` | Prefill/decode disaggregation metrics |

The upstream [inference-gateway dashboard](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.0.1/tools/dashboards/inference_gateway.json) provides EPP-specific metrics visualization.

> [!NOTE]
> The upstream dashboard may use older `inference_model_*` metric names. Current llm-d deployments use `inference_objective_*`. If panels show "No data", update the metric names in the dashboard JSON.

## Step 5: Query Metrics

Access the Prometheus UI:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090
# Open http://localhost:9090 (or https://localhost:9090 if TLS is enabled)
```

## Cleanup

```bash
./docs/monitoring/scripts/install-prometheus-grafana.sh -u -n llm-d-monitoring
```

## Troubleshooting

### Autoscaler reports "http: server gave HTTP response to HTTPS client"

The autoscaler is configured for HTTPS but Prometheus is serving HTTP. Enable TLS:

```bash
./docs/monitoring/scripts/install-prometheus-grafana.sh -u
./docs/monitoring/scripts/install-prometheus-grafana.sh --enable-tls
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
