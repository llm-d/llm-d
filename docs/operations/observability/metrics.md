# Collect Metrics

This page covers how to scrape metrics from an llm-d deployment into Prometheus (either GKE Managed Prometheus or an OSS Prometheus instance) and query them. For monitoring stack options and setup, see [Set Up the Monitoring Stack](./setup.md) first.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Prerequisites

- A running llm-d deployment with an InferencePool and model servers — see the [quickstart](../../getting-started/quickstart.md) if needed
- A monitoring stack configured — either GKE Managed Prometheus, OpenShift User Workload Monitoring, or the OSS Prometheus and Grafana stack; see [Set Up the Monitoring Stack](./setup.md)

> [!NOTE]
> TPU hardware metrics require the [GKE TPU monitoring recipe](../../../guides/recipes/observability/tpu/), which scrapes the GKE device-plugin exporter rather than the model server.
> [Monitor GKE TPUs](./tpu.md) documents metric names, units, labels, and environment checks. Metric availability depends on the GKE runtime and TPU type; the vLLM image version alone does not determine that interface.

## Step 1: Scrape Model Server Metrics

Model server metrics are enabled by default (exposed on port `8000` at `/metrics`). How metrics are collected depends on whether you use GKE automatic application monitoring or the OSS Prometheus Operator stack.

### GKE Automatic Application Monitoring (No Manual PodMonitors Required)

If you are running on GKE with [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) enabled (`--auto-monitoring-scope=ALL`; see [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry)), GKE automatically detects deployed model server workloads (including **vLLM** and **SGLang**) and provisions `PodMonitoring` (`monitoring.googleapis.com/v1`) resources labeled `source: gke-auto-monitoring` in your workload namespace. You do **not** need to manually create `PodMonitor` resources or apply the Kustomize monitoring component.

Verify the auto-generated `PodMonitoring` resource in your namespace:

```bash
kubectl get podmonitoring -n ${NAMESPACE} -l source=gke-auto-monitoring
```

Expected output:

```text
NAME                                                                     AGE
gke-ac-9b1b9035d7cd5d78ca0d37904b667bddb67b31c156d4822687e8c3e1725f0947   1m
```

### OSS Prometheus Stack / Kustomize Deployments

If you are using the OSS Prometheus Operator stack (or OpenShift User Workload Monitoring), apply the Kustomize monitoring component to create `PodMonitor` (`monitoring.coreos.com/v1`) resources:

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
```

Or include the monitoring component directly in your `kustomization.yaml`:

```yaml
components:
  - ../../../recipes/modelserver/components/monitoring       # decode PodMonitor
  # - ../../../recipes/modelserver/components/monitoring-pd  # add for prefill/decode disaggregation
```

See [`guides/recipes/modelserver/components/monitoring/`](../../../guides/recipes/modelserver/components/monitoring/) for details.

Verify the `PodMonitor` resources exist:

```bash
kubectl get podmonitors -n ${NAMESPACE}
```

Expected output:

```text
NAME                    AGE
decode                  5m
prefill                 5m
```

## Step 2: Scrape EPP and Gateway Metrics

- **GKE Gateway:** When deploying the llm-d Router in Gateway Mode with [GKE Gateway](../../infrastructure/gateway/gke.md) (`GW_PROVIDER_NAME=gke`), GKE provides an out-of-the-box [GKE Inference Gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard) in Cloud Monitoring (**Monitoring → Dashboards → Integrations → GMP → Gateway**). To also scrape the EPP's `/metrics` endpoint (port `9090`) directly into Google Cloud Managed Service for Prometheus, apply a `PodMonitoring` resource matching the EPP pod's label:

  ```yaml
  apiVersion: monitoring.googleapis.com/v1
  kind: PodMonitoring
  metadata:
    name: epp-podmonitoring
    namespace: ${NAMESPACE}
  spec:
    selector:
      matchLabels:
        llm-d.ai/igw-mode: llm-d-router-gateway # or llm-d-router-standalone in Standalone Mode
    endpoints:
      - port: metrics
        path: /metrics
        interval: 15s
  ```

- **OSS Prometheus Stack:** Pass `-f guides/recipes/router/features/monitoring.values.yaml` when installing the router Helm chart (or see the [Monitoring & Tracing Configuration](https://github.com/llm-d/llm-d-router/tree/main/config/charts#4-monitoring--tracing-configuration) section in the llm-d-router Helm chart docs) to create a `ServiceMonitor` (`monitoring.coreos.com/v1`) for the EPP.

  Verify the `ServiceMonitor` exists:

  ```bash
  kubectl get servicemonitors -n ${NAMESPACE}
  ```

  Expected output:

  ```text
  NAME                    AGE
  epp-servicemonitor      5m
  ```

## Step 3: Query Metrics

- **GKE Cloud Monitoring:** Open [Metrics Explorer](https://console.cloud.google.com/monitoring/metrics-explorer) in the Google Cloud console (select **PromQL** mode to run Prometheus queries) or view the out-of-the-box dashboards under **Monitoring → Dashboards**.
- **OSS Prometheus UI:** Port-forward the Prometheus service:

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

1. Check that scraping resources exist:
   - **GKE Managed Prometheus:** `kubectl get podmonitoring -n ${NAMESPACE}`
   - **OSS Prometheus Stack:** `kubectl get podmonitors,servicemonitors -n ${NAMESPACE}`

2. Verify the collector is scraping the targets. On the OSS Prometheus UI (`http://localhost:9090/targets`), check that model server and EPP targets show `UP`. On GKE, verify that the `PodMonitoring` status reports `ConfigurationCreateSuccess=True`.

3. Confirm pods expose metrics:

   ```bash
   MODEL_POD=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')
   kubectl port-forward -n ${NAMESPACE} ${MODEL_POD} 8000:8000
   curl http://localhost:8000/metrics | head -20
   ```

For Prometheus TLS and installation issues, see the [setup troubleshooting](./setup.md#troubleshooting).
