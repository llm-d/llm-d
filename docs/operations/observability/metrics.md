# Collect Metrics

This page covers how to scrape metrics from an llm-d deployment into Prometheus and query them. For monitoring stack setup, see [Set Up the Monitoring Stack](./setup.md).

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Prerequisites

- A running llm-d deployment with an InferencePool and model servers — see the [quickstart](../../getting-started/quickstart.md) if needed
- A monitoring stack configured — see [Set Up the Monitoring Stack](./setup.md)

> [!NOTE]
> TPU hardware metrics require the [GKE TPU monitoring recipe](../../../guides/recipes/observability/tpu/), which scrapes the GKE device-plugin exporter rather than the model server.
> [Monitor GKE TPUs](./tpu.md) documents metric names, units, labels, and environment checks. Metric availability depends on the GKE runtime and TPU type; the vLLM image version alone does not determine that interface.

## Step 1: Scrape Model Server Metrics

Model server metrics are enabled by default on port `8000` (at `/metrics` for **vLLM** and **SGLang**, and at `/prometheus/metrics` for **TensorRT-LLM**).

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

Apply the Kustomize monitoring component to create `PodMonitor` (`monitoring.coreos.com/v1`) resources for `/metrics` (vLLM and SGLang):

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
```

Or include the monitoring component directly in your `kustomization.yaml`:

```yaml
components:
  - ../../../recipes/modelserver/components/monitoring       # decode PodMonitor
  # - ../../../recipes/modelserver/components/monitoring-pd  # add for prefill/decode disaggregation
```

See [`guides/recipes/modelserver/components/monitoring/`](../../../guides/recipes/modelserver/components/monitoring/) for details (for TensorRT-LLM, set `path: /prometheus/metrics` on the `PodMonitor` endpoint).

Verify the `PodMonitor` resources exist:

```bash
kubectl get podmonitors -n ${NAMESPACE}
```

Expected output (`prefill` also appears when `monitoring-pd` is applied):

```text
NAME                    AGE
decode                  5m
```

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

When [GKE automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) is enabled (`--auto-monitoring-scope=ALL`; see [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry)), GKE automatically detects **vLLM** and **SGLang** model server workloads and provisions `PodMonitoring` (`monitoring.googleapis.com/v1`) resources labeled `source: gke-auto-monitoring` in your workload namespace. You do **not** need to manually create `PodMonitor` resources or apply the Kustomize monitoring component for vLLM or SGLang.

Verify the auto-generated `PodMonitoring` resource in your namespace:

```bash
kubectl get podmonitoring -n ${NAMESPACE} -l source=gke-auto-monitoring
```

Expected output:

```text
NAME                                                                     AGE
gke-ac-9b1b9035d7cd5d78ca0d37904b667bddb67b31c156d4822687e8c3e1725f0947   1m
```

For **TensorRT-LLM** (`trtllm-serve`), which exposes Prometheus metrics at `/prometheus/metrics` rather than `/metrics`, apply a `PodMonitoring` resource targeting `/prometheus/metrics`:

```yaml
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
  name: trtllm-decode
  namespace: ${NAMESPACE}
spec:
  selector:
    matchLabels:
      llm-d.ai/guide: optimized-baseline
      llm-d.ai/role: decode
  endpoints:
    - port: modelserver
      path: /prometheus/metrics
      interval: 15s
```

</details>
<!-- tabs:end -->

## Step 2: Scrape EPP Metrics

The router Helm chart configures EPP metrics scraping via a monitoring values file (see [Router Recipes — Enable Prometheus Monitoring](../../../guides/recipes/router/README.md#enable-prometheus-monitoring-optional)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

Layer [`guides/recipes/router/features/monitoring.values.yaml`](../../../guides/recipes/router/features/monitoring.values.yaml) when installing or upgrading the router Helm chart to create a `ServiceMonitor` (`monitoring.coreos.com/v1`) for the EPP.

Verify the `ServiceMonitor` exists:

```bash
kubectl get servicemonitors -n ${NAMESPACE}
```

Expected output:

```text
NAME                    AGE
epp-servicemonitor      5m
```

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

GKE automatic application monitoring creates `PodMonitoring` resources for model servers (`vLLM` and `SGLang`), not for the EPP deployment (`*-epp`). Layer [`guides/recipes/router/features/monitoring-gke.values.yaml`](../../../guides/recipes/router/features/monitoring-gke.values.yaml) when installing or upgrading the router Helm chart (add `--set router.monitoring.provider.gmp.autopilot=true` on GKE Autopilot) so the chart creates an authenticated `PodMonitoring` (`monitoring.googleapis.com/v1`) for the EPP (`:9090/metrics`), along with the metrics-reader `ServiceAccount`, token `Secret`, and RBAC bindings for `gmp-system`.

Verify the EPP `PodMonitoring` resource exists:

```bash
kubectl get podmonitoring -n ${NAMESPACE}
```

Expected output:

```text
NAME                 AGE
optimized-baseline   1m
```

</details>
<!-- tabs:end -->

## Step 3: Query Metrics in Prometheus

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

Port-forward the Prometheus service:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090
# Open http://localhost:9090 (or https://localhost:9090 if TLS is enabled)
```

To visualize these metrics in Grafana, see [Use Grafana Dashboards](./dashboards.md).

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

Open [Metrics Explorer](https://console.cloud.google.com/monitoring/metrics-explorer) in the Google Cloud console (select **PromQL** mode to run Prometheus queries) or view the out-of-the-box Cloud Monitoring dashboards described in [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry).

</details>
<!-- tabs:end -->

## Metric Reference

| Page | Covers |
| ---- | ------ |
| [Model Server Metrics](./model-server-metrics.md) | vLLM (including NIXL KV transfer and KV offloading) and SGLang (including HiCache) |
| [Router (EPP) Metrics](./router-metrics.md) | Requests and latency, inference pool, scheduler and plugins, flow control, prefix and multimodal cache, predicted latency |
| [Batch Gateway Metrics](./batch-gateway-metrics.md) | Batch Gateway API server, processor, and GC reconciler |
| [Track Inference Cost](./inference-cost.md) | Per-model hourly cost and cost per million tokens via OpenCost |

For ready-to-run queries, see the [PromQL Query Reference](./promql.md).

## Troubleshooting

### Metrics not appearing in Prometheus

1. Check that scraping resources exist:
   - **OSS Prometheus / OpenShift:** `kubectl get podmonitors,servicemonitors -n ${NAMESPACE}`
   - **GKE (Managed Prometheus):** `kubectl get podmonitoring -n ${NAMESPACE}`

2. Verify the collector is scraping the targets. On the OSS Prometheus UI (`http://localhost:9090/targets`), check that model server and EPP targets show `UP`. On GKE, verify that the `PodMonitoring` status reports `ConfigurationCreateSuccess=True`.

3. Confirm pods expose metrics:

   ```bash
   MODEL_POD=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')
   kubectl port-forward -n ${NAMESPACE} ${MODEL_POD} 8000:8000
   curl http://localhost:8000/metrics | head -20
   ```

For Prometheus TLS and installation issues, see the [setup troubleshooting](./setup.md#troubleshooting).
