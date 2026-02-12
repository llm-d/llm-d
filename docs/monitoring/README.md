# Observability and Monitoring in llm-d

Please join [SIG-Observability](../../SIGS.md#sig-observability) to contribute to monitoring and observability topics within llm-d.

## Enable Metrics Collection in llm-d Deployments

### Prometheus HTTPS/TLS Support

By default, Prometheus is installed with HTTP-only access. For production environments or when integrating with autoscalers that require HTTPS, you can enable TLS:

```bash
# Install Prometheus with HTTPS/TLS enabled
./scripts/install-prometheus-grafana.sh --enable-tls
```

This will:

1. Generate self-signed TLS certificates valid for 10 years
2. Create Kubernetes secrets with the certificates
3. Configure Prometheus to serve its API over HTTPS
4. Update Grafana datasource to use HTTPS

**Accessing Prometheus with TLS:**

- Internal cluster access: `https://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090`
- Port-forward access: `kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090` then access via `https://localhost:9090`

**For clients that need the CA certificate:**

```bash
kubectl get configmap prometheus-web-tls-ca -n llm-d-monitoring -o jsonpath='{.data.ca\.crt}' > prometheus-ca.crt
```

**Certificate Management:**

- Certificates are stored in the `prometheus-web-tls` secret
- CA certificate is also available in the `prometheus-web-tls-ca` ConfigMap for client use
- To regenerate certificates: delete the secret and run the installation script again with `--enable-tls`

### Platform-Specific

- If running on Google Kubernetes Engine (GKE),
  - Refer to [Google Cloud Managed Prometheus documentation](https://cloud.google.com/stackdriver/docs/managed-prometheus)
  for general guidance on how to collect metrics.
  - Enable [automatic application monitoring](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) which will automatically collect metrics for vLLM.
  - GKE provides an out of box [inference gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard).
- If running on OpenShift, User Workload Monitoring provides an accessible Prometheus Stack for scraping metrics. See the
  [OpenShift documentation](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index#enabling-monitoring-for-user-defined-projects_preparing-to-configure-the-monitoring-stack-uwm)
  to enable this feature.
- In other Kubernetes environments, Prometheus custom resources must be available in the cluster. To install a simple Prometheus and Grafana stack,
  refer to [prometheus-grafana-stack.md](./prometheus-grafana-stack.md).

### Helmfile Integration

All [llm-d guides](../../guides/README.md) have monitoring enabled by default, supporting multiple monitoring stacks depending on the environment. We provide out of box monitoring configurations for scraping the [Endpoint Picker (EPP)](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/docs/proposals/004-endpoint-picker-protocol) metrics, and vLLM metrics.

See the vLLM Metrics and EPP Metrics sections below for how to further config or disable monitoring.

### vLLM Metrics

vLLM metrics collection is enabled by default with:

```yaml
# In your ms-*/values.yaml files
decode:
  monitoring:
    podmonitor:
      enabled: true

prefill:
  monitoring:
    podmonitor:
      enabled: true
```

Upon installation, view prefill and/or decode podmonitors with:

```bash
kubectl get podmonitors -n my-llm-d-namespace
```

The vLLM metrics from prefill and decode pods will be visible from the Prometheus and/or Grafana user interface.

### EPP (Endpoint Picker) Metrics

EPP provides additional metrics for request routing, scheduling latency, and plugin performance. EPP metrics collection is enabled by default with:

- For self-installed Prometheus,

  ```yaml
  # In your gaie-*/values.yaml files
  inferenceExtension:
    monitoring:
      prometheus:
        enabled: true
  ```

  Upon installation, view EPP servicemonitors with:

  ```bash
  kubectl get servicemonitors -n my-llm-d-namespace
  ```

- For GKE managed Prometheus,

  ```yaml
  # In your gaie-*/values.yaml files
  inferenceExtension:
    monitoring:
      prometheus:
        enabled: true
  ```

EPP metrics include request rates, error rates, scheduling latency, and plugin processing times, providing insights into the inference routing and scheduling performance.

## Distributed Tracing

llm-d supports OpenTelemetry distributed tracing across vLLM, the routing proxy, and the EPP/inference scheduler. See [Distributed Tracing](./tracing/README.md) for setup instructions including a quick-start Jaeger deployment, or use the [install-jaeger.sh](./scripts/install-jaeger.sh) script for a one-command setup.

## Dashboards

Grafana dashboard raw JSON files can be imported manually into a Grafana UI. Here is a current list of community dashboards:

- [llm-d vLLM Overview dashboard](./grafana/dashboards/llm-d-vllm-overview.json)
  - General vLLM metrics overview for monitoring llm-d inference servers
- [llm-d Failure & Saturation Indicators dashboard](./grafana/dashboards/llm-d-failure-saturation-dashboard.json)
  - Key failure and saturation indicators for identifying system issues and capacity constraints
- [llm-d Diagnostic Drill-Down dashboard](./grafana/dashboards/llm-d-diagnostic-drilldown-dashboard.json)
  - Detailed diagnostic metrics for investigating performance issues
- [llm-d Performance Dashboard](./grafana/dashboards/llm-performance-kv-cache.json)
  - Performance metrics including KV cache utilization
- [P/D Coordinator Metrics dashboard](./grafana/dashboards/pd-coordinator-metrics.json)
  - Prefill/Decode disaggregation performance metrics
  - Shows vLLM E2E latency, prefill duration, decode duration, and phase breakdown
- [inference-gateway dashboard v1.0.1](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.0.1/tools/dashboards/inference_gateway.json)
  - EPP metrics
- [GKE managed inference gateway dashboard](https://cloud.google.com/kubernetes-engine/docs/how-to/customize-gke-inference-gateway-configurations#inference-gateway-dashboard)

## PromQL Query Examples

For specific PromQL queries to monitor LLM-D deployments, see:

- [Example PromQL Queries](./example-promQL-queries.md) - Ready-to-use queries for monitoring vLLM, EPP, and prefix caching metrics

## Load Testing and Error Generation

To populate metrics (especially error metrics) for testing and monitoring validation:

- [Traffic Generation Script](./scripts/generate-traffic-basic.sh) - Sends both valid and malformed requests to generate metrics
- [P/D Traffic Generator](./scripts/generate-traffic-pd.sh) - Concurrent traffic optimized for P/D disaggregation tracing

## Troubleshooting

### Autoscaler "http: server gave HTTP response to HTTPS client" Error

If your autoscaler is configured to connect to Prometheus via HTTPS but Prometheus is serving HTTP, you'll see this error:

```text
Post "https://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090/api/v1/query":
http: server gave HTTP response to HTTPS client
```

**Solution:** Enable TLS on your Prometheus installation:

```bash
# Reinstall with TLS enabled
./scripts/install-prometheus-grafana.sh --uninstall
./scripts/install-prometheus-grafana.sh --enable-tls
```

Or manually generate certificates and upgrade:

```bash
# Generate certificates
./scripts/generate-prometheus-tls-certs.sh

# Upgrade existing installation
helm upgrade llmd prometheus-community/kube-prometheus-stack \
  -n llm-d-monitoring \
  -f /tmp/prometheus-values-with-tls.yaml
```

After enabling TLS, ensure your autoscaler:

1. Uses `https://` instead of `http://` in the Prometheus URL
2. Has access to the CA certificate (available in the `prometheus-web-tls-ca` ConfigMap)
3. Is configured to either verify or skip TLS verification appropriately
