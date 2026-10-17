# Trace Requests

This guide shows how to enable [OpenTelemetry](https://opentelemetry.io/) distributed tracing across llm-d components and view traces in **Jaeger** or **Google Cloud Trace** (on GKE).

> [!NOTE]
> This guide assumes a running llm-d deployment with an InferencePool and model servers. For metrics and dashboards, see [Collect Metrics](./metrics.md) and [Use Grafana Dashboards](./dashboards.md).

Commands in this guide use `${NAMESPACE}` for the namespace where your llm-d workload runs:

```bash
export NAMESPACE=<your-llm-d-namespace>
```

## What Gets Traced

| Component | Config Method | Traced Operations |
| --- | --- | --- |
| **vLLM / SGLang** (prefill + decode) | Kustomize: container args + env vars | Inference engine spans (`llm_request`, model loading, compilation) |
| **Routing proxy** (P/D sidecar) | Kustomize: container args + env vars | KV transfer coordination |
| **EPP** | Helm: [`tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml) or [`tracing-gke.values.yaml`](../../../guides/recipes/router/features/tracing-gke.values.yaml) | Request routing, endpoint filtering/scoring, KV-cache indexing, tokenization |

All components export traces via OTLP gRPC to an OpenTelemetry Collector (`http://otel-collector:4317` for the in-namespace collector or `http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317` for GKE Managed OpenTelemetry), which filters noise (e.g., `/metrics` scraping spans), batches traces, and forwards them to Jaeger or Google Cloud Trace.

## Step 1: Set Up the Tracing Collector and Backend

<!-- tabs:start group=tracing -->
<details open>
<summary><b>Jaeger (OSS / OpenShift)</b></summary>

Deploy the OTel Collector and Jaeger into the same namespace as your llm-d workload:

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -n ${NAMESPACE}
```

> [!NOTE]
> If the [OpenTelemetry Operator](https://opentelemetry.io/docs/kubernetes/operator/) is installed, the script uses an `OpenTelemetryCollector` CR. Otherwise it deploys a standalone collector Deployment.

Wait for the collector and Jaeger to become ready, then check their Deployments. These names work for both the standalone collector and the Operator-managed collector; their Pod labels differ.

```bash
kubectl wait -n "${NAMESPACE}" --for=create deployment/otel-collector --timeout=5m
kubectl rollout status -n "${NAMESPACE}" deployment/otel-collector --timeout=5m
kubectl rollout status -n "${NAMESPACE}" deployment/jaeger --timeout=5m
kubectl get deployment -n "${NAMESPACE}" otel-collector jaeger
```

Expected output:

```text
NAME             READY   UP-TO-DATE   AVAILABLE   AGE
otel-collector   1/1     1            1           30s
jaeger           1/1     1            1           30s
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

Verify with the same rollout and Deployment checks above.

</details>
<details>
<summary><b>GKE (Google Cloud Trace)</b></summary>

Enable [GKE Managed OpenTelemetry](https://cloud.google.com/kubernetes-engine/docs/how-to/managed-otel-gke) (`--managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS`; see [GKE Monitoring & Telemetry](../../infrastructure/providers/gke/README.md#monitoring--telemetry)):

```bash
gcloud container clusters update <cluster-name> \
  --location=<region-or-zone> \
  --managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS
```

GKE deploys and manages an OpenTelemetry Collector in the `gke-managed-otel` namespace (`http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317` for OTLP gRPC, `:4318` for OTLP HTTP) with built-in authentication to [Google Cloud Trace](https://cloud.google.com/trace/docs) and automatic Kubernetes resource attribute enrichment (`k8s.cluster.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.node.name`). No collector deployment or Workload Identity IAM configuration is required in your workload namespace.

Verify the managed collector is running:

```bash
kubectl get pods,svc -n gke-managed-otel
```

Expected output:

```text
NAME                                         READY   STATUS    RESTARTS   AGE
pod/opentelemetry-collector-xxxxxxxx-xxxxx   1/1     Running   0          1m

NAME                              TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)             AGE
service/opentelemetry-collector   ClusterIP   34.118.234.183   <none>        4317/TCP,4318/TCP   1m
```

> [!NOTE]
> **Fallback (Self-Managed Collector for Cloud Trace):** If `--managed-otel-scope` is not enabled on your cluster, grant `roles/cloudtrace.agent` to the `default` ServiceAccount in `${NAMESPACE}` via Workload Identity and deploy the standalone Cloud Trace collector with `./guides/recipes/observability/install-otel-collector-jaeger.sh --gke -n ${NAMESPACE}` (which applies [`otel-collector-gke.yaml`](../../../guides/recipes/observability/tracing/otel-collector-gke.yaml) at `http://otel-collector:4317`).

</details>
<!-- tabs:end -->

## Step 2: Enable Tracing on the Model Server and Routing Proxy

Set the collector endpoint according to your Step 1 backend:

- **Jaeger (OSS / OpenShift):** `http://otel-collector:4317`
- **GKE (Managed OpenTelemetry):** `http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317` (or `http://otel-collector:4317` if using the self-managed fallback collector)

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

For GKE Managed OpenTelemetry, update the endpoint to the managed collector before sending requests:

```bash
kubectl set env -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" \
  --containers=modelserver \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

For a manual trace test, sample all model server root spans:

```bash
kubectl set env -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" \
  --containers=modelserver OTEL_TRACES_SAMPLER_ARG=1.0
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

Reapplying the tracing overlay restores its default collector endpoint and sampling ratio. For persistent endpoint or sampling customization, create another Kustomization that references the tracing overlay and patches the `modelserver` env entries by name. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to the collector's full Service DNS name when it runs in another namespace; the CLI endpoint follows that env value.

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

Set `ROUTER_RELEASE` to the Helm release name of your running router. The guides default to `${GUIDE_NAME}`; if you installed with a different release name, use that name from `helm list -n "${NAMESPACE}"`. Keep `GUIDE_NAME` unchanged: it selects the model server overlay and Pod labels in Step 2. When reusing the guide's installation command below, replace `helm install ${GUIDE_NAME}` with `helm upgrade --install "${ROUTER_RELEASE}"` and keep its chart, version, and existing values files.

```bash
export ROUTER_RELEASE="${ROUTER_RELEASE:-${GUIDE_NAME}}"
```

Layer the tracing values file for your platform when installing or upgrading the router Helm chart (see [Router Recipes — Enable Distributed Tracing](../../../guides/recipes/router/README.md#enable-distributed-tracing-optional)). Because the recipes default to a 5% sampling ratio (`samplerArg: "0.05"`), override `router.tracing.sampling.samplerArg="1.0"` when verifying with a small number of test requests:

<!-- tabs:start group=tracing -->
<details open>
<summary><b>Jaeger (OSS / OpenShift)</b></summary>

Re-run your guide's router `helm upgrade --install` command with the tracing values file ([`guides/recipes/router/features/tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml), which exports to `http://otel-collector:4317`) added after the guide's own values files:

```bash
  -f ${REPO_ROOT}/guides/recipes/router/features/tracing.values.yaml \
  --set router.tracing.sampling.samplerArg="1.0"
```

</details>
<details>
<summary><b>GKE (Google Cloud Trace)</b></summary>

Re-run your guide's router `helm upgrade --install` command with [`guides/recipes/router/features/tracing-gke.values.yaml`](../../../guides/recipes/router/features/tracing-gke.values.yaml) (`http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317`) added after the guide's own values files:

```bash
  -f ${REPO_ROOT}/guides/recipes/router/features/tracing-gke.values.yaml \
  --set router.tracing.sampling.samplerArg="1.0"
```

If your cluster uses the self-managed fallback collector from Step 1 (`install-otel-collector-jaeger.sh --gke`), layer [`guides/recipes/router/features/tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml) (`http://otel-collector:4317`) instead of `tracing-gke.values.yaml`.

</details>
<!-- tabs:end -->

Wait for the EPP rollout to complete:

```bash
kubectl rollout status deployment/${ROUTER_RELEASE}-epp -n ${NAMESPACE}
```

A ratio of `1.0` on EPP samples every request without an upstream parent. An unsampled upstream `traceparent` still prevents sampling with `parentbased_traceidratio`; increasing only the model server ratio does not change that decision.

## Step 4: View Traces

Send a request through the router using your guide's endpoint selection and served `MODEL`. For a standalone installation:

```bash
export IP=$(kubectl get service "${ROUTER_RELEASE}-epp" -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}')
kubectl run tracing-request --rm -i --restart=Never \
  --image="${CURL_TEST_IMAGE}" --namespace="${NAMESPACE}" \
  --env="IP=${IP}" --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl --fail-with-body --max-time 120 -sS -X POST "http://${IP}/v1/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"Explain distributed tracing briefly.\",\"max_tokens\":32}"'
```

If the request Pod reports `ImagePullBackOff`, set `CURL_TEST_IMAGE` to a registry mirror or an approved image with `/bin/sh` and `curl`, then retry. For a gateway installation, use the request command from its guide. Confirm a successful completion response before checking traces.

<!-- tabs:start group=tracing -->
<details open>
<summary><b>Jaeger (OSS / OpenShift)</b></summary>

Access the Jaeger UI:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

> [!TIP]
> When running `kubectl` on a remote workstation or cloud VM and opening the browser on your local machine, either forward port `16686` over SSH (`ssh -N -L 16686:localhost:16686 <remote-host>`) and open `http://localhost:16686`, or pass `--address 0.0.0.0,::` to `kubectl port-forward` and open `http://<remote-host>:16686`.

Verify traces are flowing:

1. Open the Jaeger UI (`http://localhost:16686`).
2. Select a service (e.g., `llm-d-router/epp` or `vllm-decode`).
3. Click **Find Traces**.

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

If services are listed but traces are absent from the selected time range, check that the model server, EPP, and Jaeger nodes have synchronized clocks. Spans timestamped in the future fall outside Jaeger's default search end time.

Service discovery alone does not verify trace propagation. Inspect the trace IDs, services, operations, and parent references together:

```bash
curl -fsSG http://localhost:16686/api/traces \
  --data-urlencode service=vllm-decode --data-urlencode limit=20 \
  --data-urlencode lookback=1h | \
  jq '.data[] | {traceID, services: ([.processes[].serviceName] | unique),
      spans: [.spans[] | {operationName, references}]}'
```

### Inspecting Per-Span Latency Distributions in Jaeger

In the Jaeger **Search** tab, filtering by **Operation** (such as `scoring`, `tokenize`, or `request_orchestration`) still plots the **end-to-end root trace duration** on the scatter plot rather than the child span's own duration. To inspect aggregate latency percentiles (p50/p95/p99) for individual spans:

- **Query the Jaeger REST API (`/api/traces`)**: Each span entry in `http://localhost:16686/api/traces?service=llm-d-router%2Fepp&limit=100` reports its `duration` in microseconds (`duration / 1000.0` ms), grouped by `operationName`.
- **Enable the OTel `spanmetrics` connector**: Configure the [`spanmetrics` connector](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/connector/spanmetricsconnector) in `otel-collector-config` to export per-span latency histograms (`traces_span_metrics_duration_milliseconds_bucket{service_name="...", span_name="..."}`) to Prometheus.

</details>
<details>
<summary><b>GKE (Google Cloud Trace)</b></summary>

Open [Trace Explorer](https://console.cloud.google.com/traces/explorer) in the Google Cloud console:

1. In the filter bar, filter by **ServiceName** (`llm-d-router/epp` or `vllm-decode`) and/or **SpanName** (e.g., `request`, `request_orchestration`, `run_scheduler_profile`, `filter_endpoints`, `filter_prefix_cache_affinity`, `scoring`, `pick_endpoints`, `tokenize`, or `llm_request`).
2. Inspect the **aggregate latency distribution** for the selected span in the top chart:
   - **Heatmap view**: Shows span duration density over time.
   - **Percentile view**: Plots **p50, p90, p95, and p99** latency lines for the filtered span.
3. Click any point in the heatmap to inspect the correlated waterfall span tree across `llm-d-router/epp` and `vllm-decode`.

</details>
<!-- tabs:end -->

If you only see generic `GET` spans, check that:

- The vLLM container args include `--collect-detailed-traces=all`
- The EPP deployment has `router.tracing.enabled=true` and `samplerArg` is high enough to sample your test traffic

If EPP and vLLM appear in separate traces, check `traceparent` propagation through the request path. If vLLM traces are missing, check the model server startup logs for unsupported flags or missing OpenTelemetry dependencies, verify collector connectivity, and check the sampling configuration on both components.

## Production Recommendations

- **Sampling**: Set `samplerArg` to `"0.05"` (5%, the default in [`tracing.values.yaml`](../../../guides/recipes/router/features/tracing.values.yaml)) or `"0.1"` (10%) to bound tracing overhead under production load
- **Collector**: Use the OTel Collector to batch, filter `/metrics` spans, and export traces to a persistent backend
- **Backend**: Use Google Cloud Trace on GKE, Jaeger with Elasticsearch/Cassandra storage, or Grafana Tempo for long-term retention
- **Service names**: Set `OTEL_SERVICE_NAME` per container (e.g., `vllm-decode`, `vllm-prefill`, `routing-proxy`) to distinguish roles and environments

## Environment Variable Reference

When tracing is enabled, these environment variables are set on model server and `routing-proxy` containers:

| Variable | Description |
| --- | --- |
| `OTEL_SERVICE_NAME` | Service identifier (e.g., `vllm-decode`, `vllm-prefill`, `routing-proxy`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint (`http://otel-collector:4317`) |
| `OTEL_TRACES_SAMPLER` | Sampler type (e.g., `parentbased_traceidratio`) |
| `OTEL_TRACES_SAMPLER_ARG` | Sampling ratio (`1.0` = 100%, `0.1` = 10%, `0.05` = 5%) |

## Cleanup

To disable model server tracing while keeping inference running, reapply the original overlay:

```bash
kubectl apply -n "${NAMESPACE}" -k "${MODEL_SERVER_BASE}"
kubectl rollout status -n "${NAMESPACE}" deployment \
  -l "llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode" --timeout=20m
```

Repeat the configuration check from Step 2: the tracing flags and env entries should be absent. Send another completion request to verify serving still works. If EPP tracing was enabled for this test, disable it using the same chart selection and version as Step 3:

```bash
helm upgrade "${ROUTER_RELEASE}" "${ROUTER_STANDALONE_CHART}" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}" --reuse-values --wait --timeout 20m \
  --set router.tracing.enabled=false
```

For a gateway installation, use `${ROUTER_GATEWAY_CHART}` instead.

Use the guide's cleanup commands to remove the workload, or keep it running with tracing disabled. Remove the collector and Jaeger when they are no longer needed:

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```
