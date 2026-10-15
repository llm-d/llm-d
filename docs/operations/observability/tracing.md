# Trace Requests

This guide shows how to enable [OpenTelemetry](https://opentelemetry.io/) distributed tracing across llm-d components.

> [!NOTE]
> This guide assumes a running llm-d deployment with an InferencePool and model servers. For metrics and dashboards, see [Collect Metrics](./metrics.md) and [Use Grafana Dashboards](./dashboards.md).

Commands in this guide use `${NAMESPACE}` for the namespace where your llm-d workload runs:

```bash
export NAMESPACE=<your-llm-d-namespace>
```

## What Gets Traced

| Component | Config Method | Traced Operations |
|-----------|--------------|-------------------|
| **vLLM** (prefill + decode) | Kustomize: container args + env vars | Inference engine spans |
| **Routing proxy** (P/D sidecar) | Kustomize: container args + env vars | KV transfer coordination |
| **EPP** | Helm: [`guides/recipes/router/features/tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml) | Request routing, endpoint scoring, KV-cache indexing |

All components export traces via OTLP gRPC to an OpenTelemetry Collector, which filters noise (e.g., `/metrics` scraping spans), batches traces, and forwards them to a backend like Jaeger.

## Step 1: Deploy OTel Collector and Jaeger

Deploy the OTel Collector and Jaeger into the same namespace as your llm-d workload:

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -n ${NAMESPACE}
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
kubectl apply -n ${NAMESPACE} -f guides/recipes/observability/tracing/jaeger-all-in-one.yaml \
  -f guides/recipes/observability/tracing/otel-collector.yaml

# Or with the OTel Operator installed
kubectl apply -n ${NAMESPACE} -f guides/recipes/observability/tracing/jaeger-all-in-one.yaml \
  -f guides/recipes/observability/tracing/otel-collector-operator.yaml
```

Verify with the same `kubectl get pods` commands above.

## Step 2: Enable Tracing on the Model Server and Routing Proxy

Model servers are deployed with Kustomize. Several guide overlays (such as [`guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`](../../../guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml)) include commented-out tracing blocks that can be uncommented before applying the overlay.

### Model Server (`modelserver` container)

The example below uses vLLM; SGLang and other OpenTelemetry-capable model servers use the same `OTEL_*` environment variables with `OTEL_SERVICE_NAME` set to match the engine and role. Add the engine's tracing flags to its serve command and the `OTEL_*` environment variables to the `modelserver` container:

```yaml
# Add to the modelserver container args (vLLM shown):
#   - "--otlp-traces-endpoint=http://otel-collector:4317"
#   - "--collect-detailed-traces=all"

# Add to the modelserver container env (applies to any OpenTelemetry-capable engine):
env:
- name: OTEL_SERVICE_NAME
  value: "vllm-decode"  # name per engine/role, e.g. vllm-decode, vllm-prefill, sglang-decode
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://otel-collector:4317"
- name: OTEL_TRACES_SAMPLER
  value: "parentbased_traceidratio"
- name: OTEL_TRACES_SAMPLER_ARG
  value: "1.0"
```

### Routing Proxy (`routing-proxy` sidecar, P/D disaggregation only)

For prefill/decode disaggregated deployments, enable tracing on the `routing-proxy` initContainer on the decode pod by adding `--tracing=true` to `args` and the `OTEL_*` environment variables to `env`:

```yaml
initContainers:
- name: routing-proxy
  args:
  - --tracing=true
  env:
  - name: OTEL_SERVICE_NAME
    value: "routing-proxy"
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector:4317"
  - name: OTEL_TRACES_SAMPLER
    value: "parentbased_traceidratio"
  - name: OTEL_TRACES_SAMPLER_ARG
    value: "1.0"
```

> [!NOTE]
> Updating container `args` or `env` restarts the model server pods and reloads the model. Wait for the rollout before sending test requests:
>
> ```bash
> # Deployment-based guides (e.g. optimized-baseline)
> kubectl rollout status deployment -n ${NAMESPACE} -l llm-d.ai/role
> # P/D and wide-EP guides (DisaggregatedSet)
> kubectl wait --for=condition=Available disaggregatedset -n ${NAMESPACE} --all --timeout=1800s
> ```

## Step 3: Enable Tracing on EPP

Layer [`guides/recipes/router/features/tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml) when installing or upgrading the router Helm chart (see [Router Recipes — Enable Distributed Tracing](../../../guides/recipes/router/README.md#enable-distributed-tracing-optional)):

```yaml
# guides/recipes/router/features/tracing.values.yaml
router:
  tracing:
    enabled: true
    otelExporterEndpoint: "http://otel-collector:4317"
    sampling:
      sampler: "parentbased_traceidratio"
      samplerArg: "0.05"
```

> [!TIP]
> `tracing.values.yaml` defaults to `samplerArg: "0.05"` (5% sampling) for production workloads. When verifying tracing with a small number of test requests, pass `--set router.tracing.sampling.samplerArg="1.0"` to sample 100% of requests.

## Step 4: View Traces

Access the Jaeger UI:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

> [!TIP]
> When running `kubectl` on a remote workstation or cloud VM and opening the browser on your local machine, either forward port `16686` over SSH (`ssh -N -L 16686:localhost:16686 <remote-host>`) and open `http://localhost:16686`, or pass `--address 0.0.0.0,::` to `kubectl port-forward` and open `http://<remote-host>:16686`.

Verify traces are flowing:

1. Send an inference request through llm-d
2. Open the Jaeger UI
3. Select a service (e.g., `vllm-decode`, `llm-d-router/epp`)
4. Click **Find Traces**

You should see traces with multiple spans covering the request lifecycle. You can also verify via the Jaeger API:

```bash
curl -s http://localhost:16686/api/services | jq '.data'
```

Expected output:

```json
[
  "vllm-decode",
  "llm-d-router/epp"
]
```

If you only see generic `GET` spans, check that:

- The vLLM container args include `--collect-detailed-traces all`
- The EPP image includes tracing instrumentation

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
./guides/recipes/observability/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```
