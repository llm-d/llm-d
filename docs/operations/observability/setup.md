# Set Up the Monitoring Stack

This page explains how to set up Prometheus, Grafana, and distributed tracing for an llm-d deployment. All guides reference this page — set this up once and it works across every guide.

**GKE** and **OpenShift** provide built-in managed monitoring stacks where no Prometheus installation is required. Installing the bundled open-source (OSS) Prometheus and Grafana stack is also supported on all Kubernetes clusters (including GKE and OpenShift if you prefer a self-managed Prometheus Operator and Grafana instance).

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Step 1: Set Up Prometheus

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

### OSS Prometheus and Grafana Stack

Skip this step if you already have Prometheus running in your cluster or use OpenShift User Workload Monitoring below.

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

### OpenShift User Workload Monitoring

OpenShift provides a built-in Prometheus stack via [User Workload Monitoring](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index) (Prometheus endpoint: `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`). Enable it instead of installing a separate Prometheus instance; it discovers standard `PodMonitor` and `ServiceMonitor` resources in user namespaces.

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

GKE clusters include [Google Cloud Managed Service for Prometheus (GMP)](https://cloud.google.com/stackdriver/docs/managed-prometheus) by default, so no Prometheus installation is required.
Enable [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) (`--auto-monitoring-scope=ALL`) as described in [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry) to automatically collect model server metrics and provision out-of-the-box Cloud Monitoring dashboards (including the [GKE Inference Gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard) when using GKE Gateway).

To use GMP as a Grafana data source, follow the [GMP Grafana integration guide](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query#ui-grafana).

</details>
<!-- tabs:end -->

## Manual ServiceMonitor Setup (Fallback)

> [!NOTE]
> **The recommended path is the llm-d helm charts and Kustomize components** (see [Collect Metrics](./metrics.md)), which create EPP scraping resources (`ServiceMonitor` or GMP `PodMonitoring`) when you include [`monitoring.values.yaml` or `monitoring-gke.values.yaml`](../../../guides/recipes/router/README.md#enable-prometheus-monitoring-optional) and model-server `PodMonitor` resources via the Kustomize monitoring component (or automatically on GKE via automatic application monitoring). Skip this section if you deployed that way.

Use this manual setup **only as a fallback** for workloads deployed outside the llm-d helm charts — e.g. CRD, KServe, or RHAII — where `ServiceMonitor` resources are not created for you. In that case, create them manually as shown below.

### Find Your Service Labels

ServiceMonitor selectors MUST exactly match your service labels:

```bash
# Find your EPP/Router service
kubectl get svc -n ${NAMESPACE} --show-labels

# View full labels for a specific service
kubectl get svc -n ${NAMESPACE} <epp-service-name> -o yaml
```

Note the exact `app.kubernetes.io/component` and `app.kubernetes.io/name` values.

### Create ServiceMonitors

**For EPP:**

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <epp-servicemonitor-name>
  namespace: ${NAMESPACE}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/component: <epp-component>
      app.kubernetes.io/name: <epp-name>
  namespaceSelector:
    matchNames:
      - ${NAMESPACE}
  endpoints:
    - port: metrics
      path: /metrics
      interval: 10s
      scheme: http
      bearerTokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
```

**For vLLM:**

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <vllm-servicemonitor-name>
  namespace: ${NAMESPACE}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/component: <vllm-component>
      app.kubernetes.io/name: <vllm-name>
  namespaceSelector:
    matchNames:
      - ${NAMESPACE}
  endpoints:
    - port: https
      path: /metrics
      interval: 10s
      scheme: https
      tlsConfig:
        insecureSkipVerify: true
```

Apply:

```bash
kubectl apply -f servicemonitor-epp.yaml
kubectl apply -f servicemonitor-vllm.yaml
```

### Verify

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090 &
PF_PID=$!
sleep 3
curl -s 'http://localhost:9090/api/v1/targets' | jq '.data.activeTargets[] | select(.labels.namespace=="'${NAMESPACE}'") | {job: .labels.job, health: .health}'
kill ${PF_PID}
```

If you installed with `--enable-tls`, Prometheus serves HTTPS with a self-signed certificate, so query `curl -sk 'https://localhost:9090/api/v1/targets'` instead.

Targets should show `"health": "up"`. Then proceed to Step 2 for dashboards.

## Step 2: Load Grafana Dashboards

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

```bash
./guides/recipes/observability/load-llm-d-dashboards.sh
```

Then access Grafana:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80
# Open http://localhost:3000  (login: admin / admin)
```

For the list of dashboards and how to verify the import, see [Use Grafana Dashboards](./dashboards.md).

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

When [automatic application monitoring](../../infrastructure/providers/gke/README.md#monitoring--telemetry) is enabled, out-of-the-box dashboards for vLLM, SGLang, and the [GKE Inference Gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard) are available directly in Cloud Monitoring (**Monitoring → Dashboards**). Proceed to [Collect Metrics](./metrics.md) to verify workload scraping.

</details>
<!-- tabs:end -->

## Step 3: Install Distributed Tracing (Optional)

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

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

</details>
<details>
<summary><b>GKE (Google Cloud Trace)</b></summary>

Enable [GKE Managed OpenTelemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry) (`--managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS`), which runs a managed OpenTelemetry Collector in the `gke-managed-otel` namespace (`http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317`) and exports traces directly to [Google Cloud Trace](https://cloud.google.com/trace/docs) without deploying a collector in your workload namespace.

If `--managed-otel-scope` is not enabled on your cluster, you can deploy a standalone collector configured for Cloud Trace with `./guides/recipes/observability/install-otel-collector-jaeger.sh --gke -n ${NAMESPACE}` (see [Trace Requests](./tracing.md)).

</details>
<!-- tabs:end -->

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
