# Inference Cost Tracking

This recipe installs [OpenCost](https://www.opencost.io/) with its inference cost module, enabling per-model cost attribution for llm-d workloads. Once deployed, OpenCost correlates vLLM token throughput metrics with Kubernetes allocation costs and exposes cost-per-million-tokens broken down by model and namespace.

## How it works

OpenCost's inference cost module joins two data sources:

- **vLLM Prometheus metrics** — token counts and latency, labeled by `model_name` and `namespace`
- **Kubernetes allocation costs** — CPU, GPU, and RAM costs, grouped by the `llm-d.ai/model` pod label

The join key is `model_name:namespace`. llm-d guides set `--served-model-name=<short-name>` on every `vllm serve` invocation so that the `model_name` label in vLLM metrics matches the `llm-d.ai/model` pod label exactly (e.g. `Qwen3-32B`, not `Qwen/Qwen3-32B`).

The following Prometheus metrics are produced by OpenCost after install:

| Metric | Description |
|---|---|
| `llm_total_cost` | Total infrastructure cost attributed to the model |
| `llm_cost_per_million_tokens` | Cost per million tokens (prompt + generation) |
| `llm_prompt_tokens_total` | Cumulative prompt tokens by model |
| `llm_generation_tokens_total` | Cumulative generation tokens by model |

All metrics carry `model_name`, `namespace`, and `cost_basis` labels. They are available on OpenCost's `/metrics` endpoint (port 9003) and, if a ServiceMonitor is deployed, in Prometheus.

## Deployment topology

Install OpenCost and Prometheus **once per cluster**, not once per llm-d instance. A single OpenCost deployment covers all llm-d namespaces automatically:

- OpenCost's allocation API groups costs by `model_name:namespace`, so `Qwen3-32B:llm-d-prod` and `Qwen3-32B:llm-d-staging` are tracked separately even when running the same model
- A single Prometheus with cluster-wide scraping (`serviceMonitorNamespaceSelector: {}`) collects vLLM metrics from all namespaces — no per-namespace configuration needed
- The OpenCost UI and `/inferenceCost` API give you cross-namespace cost aggregation out of the box, which is not possible if each llm-d instance has its own OpenCost

The installer deploys into a dedicated monitoring namespace (default: `llm-d-monitoring`) that is separate from your llm-d workload namespaces. When using `install-prometheus-grafana.sh` to install Prometheus, pass `--central` to configure it for cluster-wide scraping.

If your cluster already has a shared Prometheus (such as OpenShift's user workload monitoring or an existing kube-prometheus-stack), point the installer at it with `--prometheus-endpoint` instead of installing a new one.

## Prerequisites

- Prometheus is installed and scraping vLLM pods (see [install-prometheus-grafana.sh](../install-prometheus-grafana.sh))
- vLLM pods carry the `llm-d.ai/inference-serving: "true"` and `llm-d.ai/model: <short-name>` labels
- `helm`, `kubectl`, and `jq` are available on your PATH
- Access to a running Kubernetes cluster

## Installation

```bash
./guides/recipes/observability/inferencecost/install-opencost.sh
```

The installer:

1. Checks for an existing OpenCost installation — if found, validates the version and llm-d configuration, then exits
2. Detects Prometheus; if not found, offers to install `kube-prometheus-stack` alongside OpenCost
3. Prompts for infrastructure pricing (defaults to GCP us-central1 baseline — update GPU price for on-prem accuracy)
4. Deploys OpenCost with inference cost enabled, using the pricing you confirmed

### Options

| Flag | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `llm-d-monitoring` | Namespace to install OpenCost into |
| `--prometheus-endpoint` | auto-detected | Override the Prometheus service URL |
| `--prometheus-release-name` | `llmd` | Helm release name of the kube-prometheus-stack |
| `--install-prometheus` | false | Install kube-prometheus-stack alongside OpenCost |
| `--pricing-config FILE` | interactive prompt | Path to a pre-filled pricing JSON (skips interactive prompt) |
| `--cluster-id NAME` | `llm-d` | Cluster identifier label attached to cost metrics |
| `-g`, `--context FILE` | `$KUBECONFIG` | Path to a specific kubeconfig file |
| `-y`, `--yes` | false | Accept default GCP prices without interactive confirmation |
| `-u`, `--uninstall` | — | Uninstall OpenCost and remove its ConfigMaps |

### Non-interactive install

```bash
# Accept GCP default prices
./install-opencost.sh -y

# Supply your own pricing file
./install-opencost.sh --pricing-config /path/to/prices.json

# Install Prometheus and OpenCost together in one namespace
./install-opencost.sh --install-prometheus -n llm-d-monitoring
```

### Pricing config format

The installer writes a ConfigMap (`opencost-custom-pricing`) from the prices you confirm. To prepare one in advance:

```json
{
  "provider": "custom",
  "description": "on-prem pricing",
  "CPU": "0.031611",
  "spotCPU": "0.006655",
  "RAM": "0.004237",
  "spotRAM": "0.000892",
  "GPU": "0.95",
  "storage": "0.00005479452",
  "zoneNetworkEgress": "0.01",
  "regionNetworkEgress": "0.01",
  "internetNetworkEgress": "0.12"
}
```

The GPU price is the most significant input for llm-d cost attribution. Set it to your actual GPU node hourly rate divided by the number of GPUs per node.

## Verifying the installation

### Check OpenCost is running

```bash
kubectl get pods -n llm-d-monitoring -l app.kubernetes.io/name=opencost
```

### Check inference cost metrics

```bash
kubectl port-forward -n llm-d-monitoring svc/opencost 9003:9003
curl -s http://localhost:9003/metrics | grep llm_
```

Expected output includes lines like:

```
llm_cost_per_million_tokens{model_name="Qwen3-32B",namespace="llm-d",cost_basis="allocation"} 4.21
llm_total_cost{model_name="Qwen3-32B",namespace="llm-d",cost_basis="allocation"} 0.037
```

If the metrics are empty, generate some traffic first (the module requires at least one completed request window).

### Access the OpenCost UI

```bash
kubectl port-forward -n llm-d-monitoring svc/opencost 9090:9090
```

Open http://localhost:9090. The **Allocation** view shows cost broken down by `llm-d.ai/model` label. The inference cost gauges appear in Prometheus under the `llm_*` prefix.

### Query the REST API

With the port-forward above active, two on-demand cost query endpoints are available:

```bash
# Total cost by model for the last hour
curl "http://localhost:9003/inferenceCost/total?window=1h"

# Cost time series, aggregated by model, for the last 24 hours
curl "http://localhost:9003/inferenceCost/timeseries?window=24h&aggregate=model_name"
```

For the full API reference — available query parameters, filtering, aggregation options, and response schema — see the [OpenCost REST API Endpoints documentation](https://github.com/opencost/opencost/blob/main/docs/inference-cost-tracking.md#rest-api-endpoints).

### Run the llm-d config checks

Re-run the installer against an existing OpenCost deployment to validate the llm-d configuration:

```bash
./install-opencost.sh --prometheus-endpoint http://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090
```

The script checks:

1. kube-state-metrics exposes `llm-d.ai/model` in `kube_pod_labels`
2. vLLM pods carry `llm-d.ai/inference-serving=true`
3. `vllm:prompt_tokens_total` metrics are present in Prometheus
4. The `model_name` label in vLLM metrics matches `llm-d.ai/model` pod labels
5. `INFERENCE_COST_ENABLED=true` is set in the OpenCost pod

## Configuration details

### Inference cost environment variables

The installer configures OpenCost's exporter with the following environment variables:

| Variable | Value | Description |
|---|---|---|
| `INFERENCE_COST_ENABLED` | `true` | Activates the inference cost module |
| `INFERENCE_MODEL_LABEL` | `llm-d.ai/model` | Pod label used to group allocation costs by model |
| `INFERENCE_SHARED_INFRA_LABEL` | `llm-d.ai/inference-serving` | Pod label that identifies inference workloads |
| `INFERENCE_SHARED_INFRA_LABEL_VALUE` | `true` | Expected value of the shared infra label |
| `INFERENCE_KV_CACHE_BLOCK_SIZE` | `16` | Must match vLLM `--block-size`; set to `0` to disable KV cache correction |

### kube-state-metrics label allowlist

The `install-prometheus-grafana.sh` script (when used to install Prometheus for llm-d) automatically configures kube-state-metrics to expose `llm-d.ai/*` pod labels in `kube_pod_labels`. This is required for OpenCost to join allocation costs by model. If you manage Prometheus separately, add the following to your kube-prometheus-stack Helm values:

```yaml
kube-state-metrics:
  metricLabelsAllowlist:
    - pods=[llm-d.ai/role,llm-d.ai/model,llm-d.ai/accelerator-vendor,llm-d.ai/accelerator-variant,llm-d.ai/engine-type,llm-d.ai/inference-serving,llm-d.ai/managed]
    - nodes=[llm-d.ai/accelerator-vendor,llm-d.ai/accelerator-variant]
```

### Model name consistency

vLLM reports the model name it was started with as the `model_name` label in Prometheus metrics. All llm-d guides set `--served-model-name=<short-name>` (e.g. `--served-model-name=Qwen3-32B`) so that this label matches the `llm-d.ai/model` pod label value exactly. If you add a custom model server, ensure its `--served-model-name` matches its `llm-d.ai/model` label — the installer's config check (step 4 above) will catch any mismatch.

## File layout

```
inferencecost/
├── install-opencost.sh          # Installer and validator
├── manifests/
│   └── metrics-config.yaml      # OpenCost label whitelist ConfigMap
└── values/
    ├── opencost-base.yaml        # Helm values reference (applied by installer)
    └── opencost-with-prometheus.yaml  # ServiceMonitor label overlay
```

## Uninstall

```bash
./install-opencost.sh --uninstall
```

This removes the OpenCost Helm release and its ConfigMaps (`opencost-custom-pricing`, `metrics-config`) from the namespace. Prometheus and its data are not affected.
