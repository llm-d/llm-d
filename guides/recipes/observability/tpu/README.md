# GKE TPU Metrics

This recipe scrapes the existing GKE TPU device-plugin exporter and loads a
reference dashboard. Metric definitions, compatibility notes, and troubleshooting
live in [GKE TPU Observability](../../../../docs/operations/observability/tpu.md).

## Prerequisites

- A GKE TPU node pool with the device-plugin exporter exposing `/metrics` on port 2112.
- `kubectl` configured for that cluster, and `REPO_ROOT` set to this repository.
- Check your exporter's [metric interface](../../../../docs/operations/observability/tpu.md#metric-interface).
  Some runtime metrics are optional and may not appear on every TPU type.

## Stack

Use llm-d's central Prometheus Operator stack from the
[observability setup guide](../../../../docs/operations/observability/setup.md),
or an existing compatible stack. This recipe uses `monitoring.coreos.com/v1`
`PodMonitor`, not Google Managed Prometheus's `PodMonitoring` resource.
It does not install or configure the exporter.

## Scrape

```bash
kubectl apply -k "${REPO_ROOT}/guides/recipes/observability/tpu"
kubectl get pods -n kube-system -l k8s-app=tpu-device-plugin
kubectl get podmonitor -n kube-system tpu-metrics-exporter
```

The monitor selects `k8s-app: tpu-device-plugin` and scrapes `/metrics` on port
2112 every 15 seconds. It is labeled
`app.kubernetes.io/name: tpu-metrics-exporter`. If your Prometheus uses a non-empty
`podMonitorSelector`, add the labels it requires to this PodMonitor's metadata.
Its `podMonitorNamespaceSelector` must also match `kube-system` (for example, via
the monitoring namespace label configured for your stack). Preserve existing
selectors so monitoring of other components continues to work. The PodMonitor's
`spec.selector` selects exporter pods and must remain `k8s-app: tpu-device-plugin`.

Check the target in Prometheus before opening the dashboard. See
[exporter checks and troubleshooting](../../../../docs/operations/observability/tpu.md#check-the-exporter)
if discovery, scraping, or hardware metrics are missing.

## Dashboards

Load **llm-d GKE TPU Overview** with the shared loader:

```bash
"${REPO_ROOT}/guides/recipes/observability/load-llm-d-dashboards.sh"
```

Pass your monitoring namespace as the first argument if it differs from
`llm-d-monitoring`. Alternatively, import
[the dashboard JSON](../grafana/dashboards/llm-d-tpu-overview.json) in Grafana.

Select the Prometheus data source and exporter job (default:
`kube-system/tpu-metrics-exporter`; use the job from your Targets page if changed).
Start with instance, model, and topology set to **All**, then narrow the selection.
Scrape health remains visible when model/topology metrics are absent. Duty-cycle
and memory panels require optional runtime metrics; use the
[metric availability check](../../../../docs/operations/observability/tpu.md#check-the-exporter)
to distinguish missing metrics from a scrape failure. For a multi-cluster
installation, select a data source scoped to one cluster.
