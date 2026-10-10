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
| **Routing proxy** (P/D sidecar) | Kustomize: container env vars | KV transfer coordination |
| **EPP** | Helm: llm-d Router `router.tracing:` | Request routing, endpoint scoring, KV-cache indexing |

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

## Step 2: Enable Tracing on the Model Server

### vLLM Deployment Overlays

The [Optimized Baseline](../../../guides/optimized-baseline/README.md) and [Precise Prefix-Cache Routing](../../../guides/precise-prefix-cache-routing/README.md) guides provide opt-in tracing overlays for vLLM Deployments. Use the environment variables from your running guide, including `REPO_ROOT`, `GUIDE_NAME`, `NAMESPACE`, `ACCELERATOR_TYPE`, `MODEL_SERVER`, and `INFRA_PROVIDER`.

| Guide | Accelerator | Provider |
|-------|-------------|----------|
| Both guides | `gpu`, `cpu`, `tpu/v6`, `tpu/v7` | `base`, `gke` |
| Both guides | `amd`, `xpu` | `base` |
| Optimized Baseline | `amd` | `amd-ci` |
| Optimized Baseline | `npu`, `metax` | `base` |

These overlays configure containers that execute `vllm serve`. Iluvatar shell launchers, TPU dynamic-slice LeaderWorkerSets, DisaggregatedSets, routing proxies, and other serving engines require separate configuration.

Select the overlay for the same accelerator and provider as your deployment:

```bash
export MODEL_SERVER_BASE="${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}"
export MODEL_SERVER_TRACING="${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/tracing/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}"

# A missing overlay means this engine/accelerator/provider combination is unsupported.
test -f "${MODEL_SERVER_TRACING}/kustomization.yaml"
kubectl kustomize "${MODEL_SERVER_TRACING}"
kubectl apply --dry-run=server -n "${NAMESPACE}" -k "${MODEL_SERVER_TRACING}"
kubectl apply -n "${NAMESPACE}" -k "${MODEL_SERVER_TRACING}"
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

Applying the tracing overlay updates the existing model server Deployment and restarts its pods. It preserves the selected provider's configuration, model arguments, credentials, and KV-cache event settings. Keep the guide's other deployment steps, including the render Service for Precise Prefix-Cache Routing.

The shared [vLLM Deployment component](../../../guides/recipes/observability/tracing/components/vllm-deployment/kustomization.yaml) adds `--collect-detailed-traces=all` and `--otlp-traces-endpoint=$(OTEL_EXPORTER_OTLP_ENDPOINT)`. Kubernetes expands the endpoint variable when starting the container. The component exports to the same-namespace collector as `vllm-decode`, with a `parentbased_traceidratio` sampler and a `0.1` ratio for root spans. Child spans follow the parent's sampling decision.

Check the deployed configuration:

```bash
kubectl get deployment -n "${NAMESPACE}" \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" -o json | \
  jq '.items[].spec.template.spec.containers[] | select(.name == "modelserver") |
      {args, tracingEnv: [.env[] | select(.name | startswith("OTEL_"))]}'
```

For a manual trace test, sample all model server root spans:

```bash
kubectl set env -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" \
  --containers=modelserver OTEL_TRACES_SAMPLER_ARG=1.0
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

Reapplying the tracing overlay restores its sampling ratio. For persistent endpoint or sampling customization, create another Kustomization that references the tracing overlay and patches the `modelserver` env entries by name. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to the collector's full Service DNS name when it runs in another namespace; the CLI endpoint follows that env value.

### Composing the Component

The tracing overlays reference a complete accelerator/provider overlay as a resource and apply the component afterward. For example, `guides/optimized-baseline/modelserver/tracing/gpu/vllm/base/kustomization.yaml` contains:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../gpu/vllm/base
components:
  - ../../../../../../recipes/observability/tracing/components/vllm-deployment
```

Keep this outer overlay parallel to the original accelerator directories. Kustomize rejects a descendant overlay that references its own ancestor. Components run before patches in the same Kustomization, so adding the component directly to a guide's base allows the guide's full `args` patch to overwrite the tracing flags. The component checks the first container's name and command before appending flags.

### Other Workloads and Serving Engines

Configure tracing for other workloads in their own manifests. Set `OTEL_SERVICE_NAME` for the engine and role, and use the engine's tracing flags. The vLLM example is:

```yaml
# Add to the model server's serve command (vLLM shown):
#   --otlp-traces-endpoint http://otel-collector:4317
#   --collect-detailed-traces all

# Add to the container env (applies to any OpenTelemetry-capable engine):
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

## Step 3: Enable Tracing on EPP

Add the tracing configuration to your llm-d Router values:

```yaml
# In your router values file
router:
  tracing:
    enabled: true
    otelExporterEndpoint: "http://otel-collector:4317"
    sampling:
      sampler: "parentbased_traceidratio"
      samplerArg: "1.0"
```

For an existing standalone installation of either guide, upgrade the router using its current values and chart version:

```bash
helm upgrade "${GUIDE_NAME}" "${ROUTER_STANDALONE_CHART}" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}" --reuse-values --wait --timeout 20m \
  -f "${REPO_ROOT}/guides/recipes/router/features/tracing.values.yaml" \
  --set-string router.tracing.sampling.samplerArg=1.0
```

For Optimized Baseline's gateway installation, use `${ROUTER_GATEWAY_CHART}` instead. Use the chart version that is installed in your release. A ratio of `1.0` on EPP samples every request without an upstream parent. An unsampled upstream `traceparent` still prevents sampling with `parentbased_traceidratio`; increasing only the model server ratio does not change that decision.

## Step 4: View Traces

Send a request through the router using your guide's endpoint selection and served `MODEL`. For a standalone installation:

```bash
export IP=$(kubectl get service "${GUIDE_NAME}-epp" -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}')
kubectl run tracing-request --rm -i --restart=Never \
  --image="${CURL_TEST_IMAGE}" --namespace="${NAMESPACE}" \
  --env="IP=${IP}" --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl --fail-with-body -sS -X POST "http://${IP}/v1/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"Explain distributed tracing briefly.\",\"max_tokens\":32}"'
```

For a gateway installation, use the request command from its guide. Confirm a successful completion response before checking traces.

Access the Jaeger UI:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

Verify traces are flowing:

1. Send an inference request through llm-d
2. Open the Jaeger UI
3. Select a service (e.g., `vllm-decode`, `llm-d-router/epp`)
4. Click **Find Traces**

Open a trace for the request and verify that the same trace ID contains EPP routing spans and vLLM inference engine spans, with parent references connecting the spans. These single-stage overlays do not deploy a routing proxy. A separately configured P/D deployment should also include its proxy and model server roles in the same trace.

List services via the Jaeger API:

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

Service discovery alone does not verify trace propagation. Inspect the trace IDs, services, operations, and parent references together:

```bash
curl -fsSG http://localhost:16686/api/traces \
  --data-urlencode service=vllm-decode --data-urlencode limit=20 \
  --data-urlencode lookback=1h | \
  jq '.data[] | {traceID, services: ([.processes[].serviceName] | unique),
      spans: [.spans[] | {operationName, references}]}'
```

If you only see generic `GET` spans, check that:

- The vLLM container args include `--collect-detailed-traces all`
- The EPP image includes tracing instrumentation

If EPP and vLLM appear in separate traces, check `traceparent` propagation through the request path. If vLLM traces are missing, check the model server startup logs for unsupported flags or missing OpenTelemetry dependencies, verify collector connectivity, and check the sampling configuration on both components.

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

To disable model server tracing while keeping inference running, reapply the original overlay:

```bash
kubectl apply -n "${NAMESPACE}" -k "${MODEL_SERVER_BASE}"
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

Repeat the configuration check from Step 2: the tracing flags and env entries should be absent. Send another completion request to verify serving still works. If EPP tracing was enabled for this test, disable it using the same chart selection and version as Step 3:

```bash
helm upgrade "${GUIDE_NAME}" "${ROUTER_STANDALONE_CHART}" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}" --reuse-values --wait --timeout 20m \
  --set router.tracing.enabled=false
```

Use the guide's cleanup commands to remove the workload, or keep it running with tracing disabled. Remove the collector and Jaeger when they are no longer needed:

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```
