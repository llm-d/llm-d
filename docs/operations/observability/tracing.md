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

All components export traces via OTLP gRPC to an OpenTelemetry Collector (`http://otel-collector:4317` for the in-namespace collector or `http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317` for GKE Managed OpenTelemetry), which batches traces and forwards them to Jaeger or Google Cloud Trace.

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

Model servers are deployed with Kustomize. Several guide overlays (such as [`guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`](../../../guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml)) already include commented-out `# Uncomment for distributed tracing` lines that you can uncomment before running `kubectl apply -k`.

> [!NOTE]
> Modifying container `args` or `env` on an already-running model server triggers a rolling restart and model reload. Wait for `kubectl rollout status deployment/<modelserver-deployment> -n ${NAMESPACE}` to complete before sending test requests in Step 4.

Set the collector endpoint according to your Step 1 backend:

- **Jaeger (OSS / OpenShift):** `http://otel-collector:4317`
- **GKE (Managed OpenTelemetry):** `http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317` (or `http://otel-collector:4317` if using the self-managed fallback collector)

### Model Server (`vLLM` / `SGLang`)

Add the engine's tracing flags to its `serve` command and the `OTEL_*` environment variables to the `modelserver` container:

```yaml
# Add to the model server's serve command (vLLM shown):
args:
  - --otlp-traces-endpoint=http://otel-collector:4317
  - --collect-detailed-traces=all

# Add to the modelserver container env (applies to vLLM, SGLang, and other OTel-capable engines):
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

### Routing Proxy (`routing-proxy` Sidecar in P/D Disaggregation)

In Prefill/Decode (P/D) disaggregation deployments, pass `--tracing=true` and the `OTEL_*` environment variables to the `routing-proxy` sidecar container (in `patch-sidecar.yaml` or `disaggregatedset.yaml`):

```yaml
# Add to the routing-proxy initContainer/container:
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

## Step 3: Enable Tracing on EPP

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
kubectl rollout status deployment/${GUIDE_NAME}-epp -n ${NAMESPACE}
```

## Step 4: View Traces

Send a few inference requests through the router (for example, using the verification commands in your guide), then inspect the traces in your backend:

<!-- tabs:start group=tracing -->
<details open>
<summary><b>Jaeger (OSS / OpenShift)</b></summary>

Port-forward the Jaeger UI service:

```bash
kubectl port-forward -n ${NAMESPACE} svc/jaeger-collector 16686:16686
# Open http://localhost:16686
```

> [!TIP]
> If running `kubectl port-forward` on a remote workstation or Cloudtop, pass `--address 0.0.0.0,::` or forward the port over SSH from your local machine (`ssh -N -L 16686:localhost:16686 <remote-host>`).

Verify traces are flowing:

1. Open the Jaeger UI (`http://localhost:16686`).
2. Select a service (e.g., `llm-d-router/epp` or `vllm-decode`).
3. Click **Find Traces**.

You can also verify registered services via the Jaeger API:

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

```bash
./guides/recipes/observability/install-otel-collector-jaeger.sh -u -n ${NAMESPACE}
```
