# Use Grafana Dashboards

llm-d provides pre-built Grafana dashboards for common monitoring scenarios.

> [!NOTE]
> On GKE, out-of-the-box Cloud Monitoring dashboards are available without installing Grafana; see [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry). You can also connect Grafana to GMP using the [GMP Grafana integration guide](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query#ui-grafana) to use the Grafana dashboards below.

## Access Grafana

> [!NOTE]
> The commands below use namespace and service names from the bundled install script. If you use an existing Prometheus or Grafana instance, adjust the namespace and service names accordingly.

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80
# Open http://localhost:3000
# Default login: admin / admin
```

## Import Dashboards

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
llm-d-router                                      1      30s
llm-d-performance-kv-cache                        1      30s
llm-d-pd-coordinator-metrics                      1      30s
llm-d-batch-gateway-apiserver                     1      30s
llm-d-batch-gateway-processor                     1      30s
llm-d-batch-gateway-gc                            1      30s
llm-d-async-processor                             1      30s
llm-d-inference-cost                              1      30s
llm-d-tpu-overview                                1      30s
```

Or import individual dashboard JSON files manually from `guides/recipes/observability/grafana/dashboards/`:

| Dashboard | What it shows |
| ----------- | -------------- |
| `llm-d-vllm-overview.json` | General vLLM metrics overview |
| `llm-d-sglang-overview.json` | General SGLang metrics overview |
| `llm-d-tpu-overview.json` | GKE TPU exporter health and hardware metrics; see the [TPU recipe](../../../guides/recipes/observability/tpu/) |
| `llm-d-failure-saturation-dashboard.json` | Failure and saturation indicators |
| `llm-d-diagnostic-drilldown-dashboard.json` | Detailed diagnostic metrics for troubleshooting |
| `llm-d-performance-kv-cache.json` | Performance metrics including KV cache utilization |
| `llm-d-pd-coordinator-metrics.json` | Prefill/decode disaggregation metrics |
| `llm-d-router.json` | llm-d Router (EPP) metrics: inference pool, inference objective, and flow control |
| `llm-d-batch-gateway-apiserver.json` | Batch Gateway API server request rate, latency, and in-flight requests |
| `llm-d-batch-gateway-processor.json` | Batch Gateway job throughput, queue wait, worker saturation, and token usage |
| `llm-d-batch-gateway-gc.json` | Batch Gateway GC reconciler cycles, orphan recovery, and errors |
| `llm-d-async-processor.json` | Async Processor request rate and outcomes, inference latency and queue residence time, deadline proximity, broker backlog and in-process queue depth per queue, plus dispatch budget, gate decisions, in-flight requests against the worker limit, and what metric gates last read |
| `llm-d-inference-cost.json` | Per-token and hourly infrastructure cost tracking via OpenCost (requires [inference cost tracking](../../../guides/recipes/observability/inferencecost/README.md)) |

The three Batch Gateway dashboards are only useful if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md), and the Async Processor dashboard only if you deployed the [asynchronous processing guide](../../../guides/batch-serving/asynchronous-processing/README.md) with the chart's `ap.podMonitor.enabled=true`; each has a `namespace` variable to select the namespace it runs in.

For the metrics behind these panels, see [Model Server Metrics](./model-server-metrics.md), [Router (EPP) Metrics](./router-metrics.md), [Batch Gateway Metrics](./batch-gateway-metrics.md), [Async Processor metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics), and [Track Inference Cost](./inference-cost.md).

## Troubleshooting

### Grafana dashboards show "No data"

1. Verify the Grafana datasource points to the correct Prometheus URL
2. Check that metrics are flowing in Prometheus first (use the Prometheus UI)
3. If using TLS, ensure the Grafana datasource is configured for HTTPS with the correct CA certificate
