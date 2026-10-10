# Use Grafana Dashboards

llm-d provides pre-built Grafana dashboards for common monitoring scenarios.

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
llm-d-performance-kv-cache                        1      30s
llm-d-pd-coordinator-metrics                      1      30s
llm-d-batch-gateway-apiserver                     1      30s
llm-d-batch-gateway-processor                     1      30s
llm-d-batch-gateway-gc                            1      30s
llm-d-inference-cost                              1      30s
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
| `llm-d-inference-gateway.json` | Inference Gateway (EPP) metrics: inference pool, inference objective, and flow control |
| `llm-d-batch-gateway-apiserver.json` | Batch Gateway API server request rate, latency, and in-flight requests |
| `llm-d-batch-gateway-processor.json` | Batch Gateway job throughput, queue wait, worker saturation, and token usage |
| `llm-d-batch-gateway-gc.json` | Batch Gateway GC reconciler cycles, orphan recovery, and errors |
| `llm-d-inference-cost.json` | Per-token and hourly infrastructure cost tracking via OpenCost (requires [inference cost tracking](../../../guides/recipes/observability/inferencecost/README.md)) |

The three Batch Gateway dashboards are only useful if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md); each has a `namespace` variable to select the namespace it runs in.

For the metrics behind these panels, see [Model Server Metrics](./model-server-metrics.md), [Router (EPP) Metrics](./router-metrics.md), [Batch Gateway Metrics](./batch-gateway-metrics.md), and [Track Inference Cost](./inference-cost.md).

## Troubleshooting

### Grafana dashboards show "No data"

1. Verify the Grafana datasource points to the correct Prometheus URL
2. Check that metrics are flowing in Prometheus first (use the Prometheus UI)
3. If using TLS, ensure the Grafana datasource is configured for HTTPS with the correct CA certificate
