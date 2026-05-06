# Distributed Tracing

This guide shows how to enable [OpenTelemetry](https://opentelemetry.io/) distributed tracing across llm-d components.

> [!NOTE]
> This guide assumes a running llm-d deployment with an InferencePool and model servers. For metrics and dashboards, see [Metrics](metrics.md).

Commands in this guide use `${NAMESPACE}` for the namespace where your llm-d workload runs:

```bash
export NAMESPACE=<your-llm-d-namespace>
```

## What Gets Traced

| Component | Config Method | Traced Operations |
|-----------|--------------|-------------------|
| **vLLM** (prefill + decode) | Helm: ModelService `tracing:` / Kustomize: container args + env vars | Inference engine spans |
| **Routing proxy** (P/D sidecar) | Helm: ModelService `tracing:` / Kustomize: container env vars | KV transfer coordination |
| **EPP** | Helm: GAIE `inferenceExtension.tracing:` | Request routing, endpoint scoring, KV-cache indexing |

All components export traces via OTLP gRPC to an OpenTelemetry Collector, which filters noise (e.g., `/metrics` scraping spans), batches traces, and forwards them to a backend like Jaeger.

## Step 1: Deploy OTel Collector and Jaeger

Deploy the OTel Collector and Jaeger into the same namespace as your llm-d workload:

```bash
./docs/monitoring/scripts/install-otel-collector-jaeger.sh -n ${NAMESPACE}
```

> [!NOTE]
> If the [OpenTelemetry Operator](https://opentelemetry.io/docs/kubernetes/operator/) is installed, the script uses an `OpenTelemetryCollector` CR. Otherwise it deploys a standalone collector Deployment.

Verify the components are running:

```bash
kubectl get pods -n ${NAMESPACE} -l app=otel-collector
kubectl get pods -n ${NAMESPACE} -l app=jaeger
```

Expected output:

```text
NAME                              READY   STATUS    RESTARTS   AGE
otel-collector-xxxxxxxxx-xxxxx    1/1     Running   0          30s

NAME                      READY   STATUS    RESTARTS   AGE
jaeger-xxxxxxxxx-xxxxx    1/1     Running   0          30s
```

### Manual Deployment

If you prefer to apply manifests directly:

```bash
# Standalone collector (no operator)
kubectl apply -n ${NAMESPACE} -f docs/monitoring/tracing/jaeger-all-in-one.yaml \
  -f docs/monitoring/tracing/otel-collector.yaml

# Or with the OTel Operator installed
kubectl apply -n ${NAMESPACE} -f docs/monitoring/tracing/jaeger-all-in-one.yaml \
  -f docs/monitoring/tracing/otel-collector-operator.yaml
```

Verify with the same `kubectl get pods` commands above.

## Step 2: Enable Tracing on vLLM and Routing Proxy

Configuration varies by deployment method.

### Option A: Helm Values

All chart defaults point to `http://otel-collector:4317` (same namespace). Enable tracing in your model service values:

```yaml
# In your ms-*/values.yaml
tracing:
  enabled: true
  otlpEndpoint: "http://otel-collector:4317"
  sampling:
    sampler: "parentbased_traceidratio"
    samplerArg: "1.0"  # 100% for dev; use "0.1" (10%) in production
```

This injects `--otlp-traces-endpoint` and `--collect-detailed-traces` args into vLLM, and `OTEL_*` environment variables into both vLLM and routing-proxy containers.

### Option B: Kustomize / Raw Manifests

For kustomize deployments or raw manifests, add tracing flags to your `vllm serve` command and OTEL env vars to the container:

```yaml
# Add to vllm serve command:
#   --otlp-traces-endpoint http://otel-collector:4317
#   --collect-detailed-traces all

# Add to the container env:
env:
- name: OTEL_SERVICE_NAME
  value: "vllm-decode"  # or "vllm-prefill"
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://otel-collector:4317"
- name: OTEL_TRACES_SAMPLER
  value: "parentbased_traceidratio"
- name: OTEL_TRACES_SAMPLER_ARG
  value: "1.0"
```

## Step 3: Enable Tracing on EPP

Add the tracing configuration to your GAIE values:

```yaml
# In your gaie-*/values.yaml
inferenceExtension:
  tracing:
    enabled: true
    otelExporterEndpoint: "http://otel-collector:4317"
    sampling:
      sampler: "parentbased_traceidratio"
      samplerArg: "1.0"
```

## Step 4: View Traces

Access the Jaeger UI:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

Verify traces are flowing:

1. Send an inference request through llm-d
2. Open the Jaeger UI
3. Select a service (e.g., `vllm-decode`, `llm-d-inference-scheduler`)
4. Click **Find Traces**

You should see traces with multiple spans covering the request lifecycle. You can also verify via the Jaeger API:

```bash
curl -s http://localhost:16686/api/services | jq '.data'
```

Expected output:

```json
[
  "vllm-decode",
  "llm-d-inference-scheduler"
]
```

If you only see generic `GET` spans, check that:
- The vLLM container args include `--collect-detailed-traces all`
- The EPP image includes tracing instrumentation (`llm-d-inference-scheduler`, not upstream `epp`)

## Production Recommendations

- **Sampling**: Set `samplerArg` to `"0.1"` (10%) or lower to reduce overhead
- **Collector**: Use a collector to batch, filter, and route traces to a persistent backend
- **Backend**: Use Jaeger with Elasticsearch/Cassandra storage, or Grafana Tempo for long-term retention
- **Service names**: Set `OTEL_SERVICE_NAME` per container (e.g., `vllm-decode-prod`, `epp-us-east`) to distinguish clusters and environments

## Environment Variable Reference

When tracing is enabled, these environment variables are set on vLLM and routing-proxy containers:

| Variable | Description |
|----------|-------------|
| `OTEL_SERVICE_NAME` | Service identifier (e.g., `vllm-decode`, `routing-proxy`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint (`http://otel-collector:4317`) |
| `OTEL_TRACES_SAMPLER` | Sampler type (e.g., `parentbased_traceidratio`) |
| `OTEL_TRACES_SAMPLER_ARG` | Sampling ratio (`1.0` = 100%, `0.1` = 10%) |

## Cleanup

```bash
./docs/monitoring/scripts/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```
