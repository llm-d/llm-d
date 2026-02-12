# Distributed Tracing

This guide shows how to enable [OpenTelemetry](https://opentelemetry.io/) distributed tracing across llm-d components.

## Components

| Component | Chart / Config | What gets traced |
|---|---|---|
| **vLLM** (prefill + decode) | ModelService `tracing:` | Inference engine spans |
| **Routing proxy** (P/D sidecar) | ModelService `tracing:` | KV transfer coordination |
| **EPP / Inference Scheduler** | GAIE `inferenceExtension.tracing:` | Request routing, endpoint scoring, KV-cache indexing |

All components export traces via OTLP gRPC to an OpenTelemetry Collector, which forwards them to a backend (Jaeger, Tempo, etc.).

## Quick Start: Deploy Jaeger

The simplest way to view traces is to deploy Jaeger all-in-one. This works on both Kubernetes and OpenShift.

You can use the install script or apply the manifest directly:

```bash
# Option A: Use the install script
../scripts/install-jaeger.sh

# Option B: Apply the manifest directly
kubectl create namespace observability
kubectl apply -n observability -f jaeger-all-in-one.yaml
```

Access the Jaeger UI:

```bash
kubectl port-forward -n observability svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

> **Note:** This is an in-memory deployment for development and testing. For production, use the [Jaeger Operator](https://www.jaegertracing.io/docs/latest/operator/) or a managed backend like Grafana Tempo.

## Enable Tracing

### ModelService (vLLM + routing proxy)

Add or uncomment the `tracing:` section in your `ms-*/values.yaml`:

```yaml
tracing:
  enabled: true
  otlpEndpoint: "http://jaeger-collector.observability.svc.cluster.local:4317"
  sampling:
    sampler: "parentbased_traceidratio"
    samplerArg: "1.0"  # 100% for dev; use "0.1" (10%) in production
  vllm:
    collectDetailedTraces: "all"  # options: "all", "model", "scheduler"
```

This automatically injects the required `--otlp-traces-endpoint` and `--collect-detailed-traces` args into vLLM, and `OTEL_*` environment variables into both vLLM and routing-proxy containers.

### GAIE / EPP (Inference Scheduler)

Add or uncomment the `tracing:` section under `inferenceExtension:` in your `gaie-*/values.yaml`:

```yaml
inferenceExtension:
  tracing:
    enabled: true
    otelExporterEndpoint: "http://jaeger-collector.observability.svc.cluster.local:4317"
    sampling:
      sampler: "parentbased_traceidratio"
      samplerArg: "1.0"
```

### Kustomize / Raw Manifests

For guides that use raw manifests (e.g., `wide-ep-lws`, `recipes/vllm`), add the tracing args directly to your vLLM container:

```yaml
# vLLM args
args:
  - vllm serve my-model
    --otlp-traces-endpoint http://jaeger-collector.observability.svc.cluster.local:4317
    --collect-detailed-traces all

# Environment variables
env:
- name: OTEL_SERVICE_NAME
  value: "vllm-decode"
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://jaeger-collector.observability.svc.cluster.local:4317"
- name: OTEL_TRACES_EXPORTER
  value: "otlp"
- name: OTEL_TRACES_SAMPLER
  value: "parentbased_traceidratio"
- name: OTEL_TRACES_SAMPLER_ARG
  value: "1.0"
```

## OpenTelemetry Collector (Optional)

If you need trace processing (filtering, batching, multi-backend export), deploy an OTel Collector between your components and Jaeger. Without a collector, components can export directly to Jaeger's OTLP endpoint.

### Filtering Metrics Scraping Noise

When both Prometheus metrics and tracing are enabled, Prometheus scrapes of `/metrics` generate trace spans. Filter these out by deploying the collector:

```bash
kubectl apply -n observability -f otel-collector.yaml
```

When using a collector, point your `otlpEndpoint` / `otelExporterEndpoint` values to the collector service instead of Jaeger directly.

## Verifying Traces

1. Send an inference request through llm-d
2. Open the Jaeger UI (`http://localhost:16686`)
3. Select a service (e.g., `vllm-decode`, `llm-d-inference-scheduler`) and click **Find Traces**
4. You should see spans for inference requests, routing decisions, and KV cache operations

If you only see generic `GET` spans, check that:
- `collectDetailedTraces` is set to `"all"` for vLLM
- The EPP/inference-scheduler image includes tracing instrumentation (`llm-d-inference-scheduler`, not upstream `epp`)

## Production Recommendations

- **Sampling**: Set `samplerArg` to `"0.1"` (10%) or lower to reduce overhead
- **Collector**: Use a collector to batch, filter, and route traces to a persistent backend
- **Backend**: Use Jaeger with Elasticsearch/Cassandra storage, or Grafana Tempo for long-term retention
- **Service names**: Customize via `tracing.serviceNames` in ModelService values to distinguish clusters/environments

## Reference: Injected Environment Variables

When tracing is enabled via the ModelService chart, these are set automatically:

| Variable | vLLM | Routing Proxy | Description |
|---|---|---|---|
| `OTEL_SERVICE_NAME` | yes | yes | Service identifier |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | yes | yes | Collector endpoint |
| `OTEL_TRACES_EXPORTER` | yes | yes | Set to `otlp` |
| `OTEL_TRACES_SAMPLER` | yes | yes | Sampler type |
| `OTEL_TRACES_SAMPLER_ARG` | yes | yes | Sampling ratio |
