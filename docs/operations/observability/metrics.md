# Collect Metrics

This page covers how to scrape metrics from an llm-d deployment into Prometheus and query them. For Prometheus and Grafana installation, see [Set Up the Monitoring Stack](./setup.md) first.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Prerequisites

- A running llm-d deployment with an InferencePool and model servers — see the [quickstart](../../getting-started/quickstart.md) if needed
- Prometheus and Grafana installed — see [Set Up the Monitoring Stack](./setup.md)

> [!NOTE]
> TPU hardware metrics require the [GKE TPU monitoring recipe](../../../guides/recipes/observability/tpu/), which scrapes the GKE device-plugin exporter rather than the model server.
> [Monitor GKE TPUs](./tpu.md) documents metric names, units, labels, and environment checks. Metric availability depends on the GKE runtime and TPU type; the vLLM image version alone does not determine that interface.

## Step 1: Scrape Model Server Metrics

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

## Step 2: Scrape EPP Metrics

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

## Step 3: Query Metrics in Prometheus

Access the Prometheus UI:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090
# Open http://localhost:9090 (or https://localhost:9090 if TLS is enabled)
```

## Metric Reference

| Page | Covers |
| ---- | ------ |
| [Model Server Metrics](./model-server-metrics.md) | vLLM (including NIXL KV transfer and KV offloading) and SGLang (including HiCache) |
| [Router (EPP) Metrics](./router-metrics.md) | Requests and latency, inference pool, scheduler and plugins, flow control, prefix and multimodal cache, predicted latency |
| [Batch Gateway Metrics](./batch-gateway-metrics.md) | Batch Gateway API server, processor, and GC reconciler |
| [Track Inference Cost](./inference-cost.md) | Per-model hourly cost and cost per million tokens via OpenCost |

To visualize these metrics, see [Use Grafana Dashboards](./dashboards.md). For ready-to-run queries, see the [PromQL Query Reference](./promql.md).

## Troubleshooting

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

For Prometheus TLS and installation issues, see the [setup troubleshooting](./setup.md#troubleshooting).
