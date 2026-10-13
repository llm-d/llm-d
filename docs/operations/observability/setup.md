# Set Up the Monitoring Stack

This page explains how to set up Prometheus, Grafana, and distributed tracing for an llm-d deployment. All guides reference this page — set this up once and it works across every guide.

Choose a monitoring stack for your cluster:

- **Managed Monitoring (GKE and OpenShift)** — GKE and OpenShift provide built-in managed monitoring stacks, so no Prometheus installation is required:
  - **GKE:** Clusters include [Google Cloud Managed Service for Prometheus (GMP)](https://cloud.google.com/stackdriver/docs/managed-prometheus) by default. When [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) is enabled, GKE automatically collects metrics and configures out-of-the-box Cloud Monitoring dashboards (including the [GKE Inference Gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard) when using GKE Gateway). See [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry) and [Collect Metrics](./metrics.md). To use GMP as a Grafana data source, follow the [GMP Grafana integration guide](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query#ui-grafana).
  - **OpenShift:** OpenShift provides a built-in Prometheus stack via [User Workload Monitoring](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index) (Prometheus endpoint: `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`). Enable it instead of installing a separate Prometheus instance; it discovers standard `PodMonitor` and `ServiceMonitor` resources in user namespaces.
- **Open-Source (OSS) Monitoring Stack** — Installing the bundled OSS Prometheus and Grafana stack via `install-prometheus-grafana.sh` (Steps 1 and 2 below) is supported on all Kubernetes clusters, including GKE and OpenShift if you prefer a self-managed Prometheus Operator and Grafana instance.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Step 1: Install the OSS Prometheus and Grafana Stack

Skip this step if you are using GKE Managed Prometheus, OpenShift User Workload Monitoring, or an existing Prometheus installation in your cluster.

```bash
# Install Prometheus + Grafana into the llm-d-monitoring namespace
./guides/recipes/observability/install-prometheus-grafana.sh
```

For HTTPS/TLS (required by autoscalers like WVA):

```bash
./guides/recipes/observability/install-prometheus-grafana.sh --enable-tls
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

## Step 2: Load Grafana Dashboards (OSS Stack)

```bash
./guides/recipes/observability/load-llm-d-dashboards.sh
```

Then access Grafana:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80
# Open http://localhost:3000  (login: admin / admin)
```

For the list of dashboards and how to verify the import, see [Use Grafana Dashboards](./dashboards.md). Once your monitoring stack is ready, proceed to [Collect Metrics](./metrics.md) to configure metric collection for your workloads.

## Step 3: Install Distributed Tracing (Optional)

Deploy the OTel Collector and Jaeger into the same namespace as your llm-d workload:

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -n ${NAMESPACE}
```

Then access the Jaeger UI:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

To link the exemplar dots on the latency panel to their traces, re-run the Step 1 installer the same way you ran it before. It finds Jaeger and adds the Jaeger datasource and the exemplar link to the existing install:

```bash
./guides/recipes/observability/install-prometheus-grafana.sh
```

If Jaeger runs in more than one namespace, set `TRACING_NAMESPACE` to the one to link. If you open the Jaeger UI somewhere other than `http://localhost:16686`, set `JAEGER_UI_URL`.

For full tracing configuration across vLLM, the routing proxy, and the EPP, see [Trace Requests](./tracing.md).

## Cleanup

```bash
# Remove Prometheus and Grafana
./guides/recipes/observability/install-prometheus-grafana.sh -u -n llm-d-monitoring

# Remove OTel Collector and Jaeger
./guides/recipes/observability/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```

## Troubleshooting

### Autoscaler reports "http: server gave HTTP response to HTTPS client"

The autoscaler is configured for HTTPS but Prometheus is serving HTTP. Enable TLS:

```bash
./guides/recipes/observability/install-prometheus-grafana.sh -u
./guides/recipes/observability/install-prometheus-grafana.sh --enable-tls
```

### Metrics not appearing in Prometheus

See [Collect Metrics → Troubleshooting](./metrics.md#troubleshooting).

### Grafana dashboards show "No data"

See [Use Grafana Dashboards → Troubleshooting](./dashboards.md#troubleshooting).
