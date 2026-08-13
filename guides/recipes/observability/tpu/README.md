# GKE TPU Metrics

This recipe configures the Prometheus Operator to scrape the existing GKE TPU
device-plugin metrics exporter. It does not install or configure the exporter.

It is intended for llm-d's central `kube-prometheus-stack` monitoring setup.
Apply it after the TPU node pool and the monitoring stack are available:

```bash
kubectl apply -k ${REPO_ROOT}/guides/recipes/observability/tpu
```

Verify that the exporter pods and the PodMonitor are present:

```bash
kubectl get pods -n kube-system -l k8s-app=tpu-device-plugin
kubectl get podmonitor -n kube-system tpu-metrics-exporter
```

The monitor selects the documented GKE device-plugin label and scrapes
`/metrics` on port `2112`.

The PodMonitor is labeled
`app.kubernetes.io/name: tpu-metrics-exporter`. When using a non-empty
`podMonitorSelector`, configure it to match this label. The
`podMonitorNamespaceSelector` must also include the `kube-system` namespace.
Otherwise, the PodMonitor may be created successfully without being discovered
by Prometheus.
