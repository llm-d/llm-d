# Observability Setup

This page explains how to set up Prometheus, Grafana, and distributed tracing for an llm-d deployment. All guides reference this page — set this up once and it works across every guide.

> [!NOTE]
> Commands in this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Step 1: Install Prometheus and Grafana

Skip this step if you already have Prometheus running in your cluster.

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

### Platform-specific notes

#### OpenShift

OpenShift provides a built-in Prometheus stack via User Workload Monitoring. Enable it instead of installing a separate Prometheus:

- See the [OpenShift monitoring documentation](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index) to enable User Workload Monitoring
- Prometheus endpoint: `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`

#### GKE

GKE clusters include [Google Managed Prometheus (GMP)](https://cloud.google.com/stackdriver/docs/managed-prometheus) by default. GKE also provides a built-in [inference gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard).

To use GMP as a Grafana data source, follow the [GMP Grafana integration guide](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/query#ui-grafana).

## Manual ServiceMonitor Setup (Fallback)

> [!NOTE]
> **The recommended path is the llm-d helm charts**, which create ServiceMonitors automatically when you include `monitoring.values.yaml`. Skip this section if you deployed that way.

Use this manual setup **only as a fallback** for workloads deployed outside the llm-d helm charts — e.g. CRD, KServe, or RHAII — where ServiceMonitors are not created for you. In that case, create them manually as shown below.

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
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090
curl -sk 'https://localhost:9090/api/v1/targets' | jq '.data.activeTargets[] | select(.labels.namespace=="'${NAMESPACE}'") | {job: .labels.job, health: .health}'
```

Targets should show `"health": "up"`. Then proceed to Step 2 for dashboards.

## Step 2: Load Grafana Dashboards

```bash
./guides/recipes/observability/load-llm-d-dashboards.sh
```

Verify dashboards were imported:

```bash
kubectl get configmaps -n llm-d-monitoring -l grafana_dashboard=1
```

Then access Grafana:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80
# Open http://localhost:3000  (login: admin / admin)
```

Available dashboards:

| Dashboard | What it shows |
|-----------|--------------|
| `llm-d-vllm-overview` | General vLLM metrics overview |
| `llm-d-sglang-overview` | General SGLang metrics overview |
| `llm-d-failure-saturation-dashboard` | Key failure and saturation indicators |
| `llm-d-diagnostic-drilldown-dashboard` | Detailed diagnostic metrics for troubleshooting |
| `llm-d-performance-kv-cache` | KV cache utilization and performance |
| `llm-d-pd-coordinator-metrics` | Prefill/decode disaggregation metrics |

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

For full tracing configuration across vLLM, the routing proxy, and the EPP, see [Distributed Tracing](./tracing.md).

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

1. Check that PodMonitors and ServiceMonitors exist:

   ```bash
   kubectl get podmonitors,servicemonitors -n ${NAMESPACE}
   ```

2. Open `http://localhost:9090/targets` (after port-forwarding Prometheus) and check that vLLM and EPP targets show `UP`

3. Confirm pods expose metrics:

   ```bash
   VLLM_POD=$(kubectl get pods -n ${NAMESPACE} -l app=my-model -o jsonpath='{.items[0].metadata.name}')
   kubectl port-forward -n ${NAMESPACE} ${VLLM_POD} 8000:8000
   curl http://localhost:8000/metrics | head -20
   ```

### Grafana dashboards show "No data"

1. Verify the Grafana datasource points to the correct Prometheus URL
2. Check that metrics are flowing in Prometheus first
3. If using TLS, ensure the Grafana datasource is configured for HTTPS with the correct CA certificate
