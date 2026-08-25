# Inference Cost Tracking

This recipe installs [OpenCost](https://www.opencost.io/) with its [inference cost module](https://github.com/opencost/opencost/blob/develop/docs/inference-cost-tracking.md), enabling per-model cost attribution for llm-d workloads. Once deployed, OpenCost correlates vLLM token throughput metrics with Kubernetes allocation costs and exposes overall costs and cost-per-million-tokens.

## Why inference cost tracking?

llm-d already collects rich performance metrics via vLLM (token throughput, latency, queue depth) and DCGM (GPU utilization, memory). Those metrics tell you **how fast** a model is serving but not **what it costs** — they carry no knowledge of GPU pricing, CPU/memory allocation, or how infrastructure costs should be split across models sharing the same nodes.

Bridging that gap requires joining two separate data sources that have historically lived in different systems:

| Data source | What it provides | What it lacks |
|---|---|---|
| vLLM Prometheus metrics | Token counts, latency, cache hit rate | Infrastructure cost per token |
| Kubernetes allocation (CPU/GPU/RAM) | Infrastructure cost per pod | Which model generated the cost |

OpenCost, with its new inference capabilities, provides exactly this join. It attributes Kubernetes infrastructure costs (CPU, GPU, RAM) to pods via the `llm-d.ai/model` pod label, correlates them with vLLM's `model_name` metric label, and publishes the result as `llm_*` Prometheus gauges. The outcome is metrics that neither system could produce alone:

- **Cost per million tokens** — the unit economics of serving each model, broken down by prompt vs generation tokens
- **Hourly infrastructure cost rate** — actual GPU+CPU+RAM spend attributed per model
- **Cache savings fraction** — the cost reduction achieved by KV-cache hits, making the value of prefix caching visible in financial terms

This enables use cases that raw performance metrics cannot support: chargeback across teams, cost-based routing decisions, comparing self-hosted vs commercial API costs, and right-sizing GPU allocations. For the full motivation and design rationale, see the [inference cost proposal](https://github.com/simanadler/llm-d/blob/5c5bd7c5981ea155a9059787a63ec0ec728d409e/docs/proposals/inference-costs.md).

## How it works

OpenCost's inference cost module joins two data sources:

- **vLLM Prometheus metrics** — token counts and latency, labeled by `model_name` and `namespace`
- **Kubernetes allocation costs** — CPU, GPU, and RAM costs, grouped by the `llm-d.ai/model` pod label

The join key is `model_name:namespace`. llm-d guides set `--served-model-name=<short-name>` on every `vllm serve` invocation so that the `model_name` label in vLLM metrics matches the `llm-d.ai/model` pod label exactly (e.g. `Qwen3-32B`, not `Qwen/Qwen3-32B`).

The following Prometheus metrics are produced by OpenCost after install:

| Metric | Labels | Description |
|---|---|---|
| `llm_total_hourly_cost` | `model_name`, `model_version`, `namespace`, `cost_basis`, `workload_type` | Instantaneous hourly infrastructure cost rate ($/hour) attributed to the model — not a cumulative counter. `cost_basis` is `allocation` (max(request,usage) × price + idle/shared share; use for chargeback) or `usage` (actual consumption only, excludes idle and shared infra; use for efficiency analysis) |
| `llm_cost_per_million_tokens` | `model_name`, `model_version`, `namespace`, `cost_basis`, `phase`, `allocation_method`, `workload_type` | Cost per 1M tokens. `phase` is empty for blended (input+output combined), `prompt` for input-only, or `generation` for output-only. `allocation_method` is `compute_time` (split by vLLM prefill/decode time), `prefix_caching_off` (time-based split, prefix caching disabled), `multiplier` (fixed 2.5× ratio, timing metrics unavailable), or empty (no tokens processed or join failed) |
| `llm_cache_savings_fraction` | `model_name`, `model_version`, `namespace`, `workload_type` | Fraction of prompt tokens served from the KV cache (0–1); zero when prefix caching is disabled, no cache hits occurred, or `vllm:prefix_cache_hits_total` is missing |

**Note:** The `workload_type` label is currently always `inference`. Future values may include `training`, `fine-tuning`, etc.

All metrics are available on OpenCost's `/metrics` endpoint (port 9003) and, if a ServiceMonitor is deployed, in Prometheus.

## Deployment topology

Install OpenCost and Prometheus **once per cluster**, not once per llm-d instance. A single OpenCost deployment covers all llm-d namespaces automatically:

- OpenCost's allocation API groups costs by `model_name:namespace`, so `Qwen3-32B:llm-d-prod` and `Qwen3-32B:llm-d-staging` are tracked separately even when running the same model
- A single Prometheus with cluster-wide scraping (`serviceMonitorNamespaceSelector: {}`) collects vLLM metrics from all namespaces — no per-namespace configuration needed
- The OpenCost UI and `/inferenceCost` API give you cross-namespace cost aggregation out of the box, which is not possible if each llm-d instance has its own OpenCost

The installer deploys into a dedicated monitoring namespace (default: `llm-d-monitoring`) that is separate from your llm-d workload namespaces. `install-prometheus-grafana.sh` installs Prometheus with cluster-wide scraping enabled by default, so no extra flags are needed when using it alongside this installer.

If your cluster already has a shared Prometheus (such as OpenShift's user workload monitoring or an existing kube-prometheus-stack), point the installer at it with `--prometheus-endpoint` instead of installing a new one.

## Prerequisites

- Prometheus is installed and scraping vLLM pods (see [install-prometheus-grafana.sh](../install-prometheus-grafana.sh))
- Model-serving pods carry the `llm-d.ai/model: <short-name>` label
- Shared infrastructure pods (router/EPP/gateway) carry the `llm-d.ai/inference-shared: "true"` label
- `helm`, `kubectl`, and `jq` are available on your PATH
- Access to a running Kubernetes cluster

## Installation

```bash
./guides/recipes/observability/inferencecost/install-opencost.sh --image "ghcr.io/opencost/opencost:develop-latest@sha256:e0c09b268d8243c45323fffec8ceea1434e9f7e982905af651b1adebfc7e3135"
```

The installer:

1. Checks for an existing OpenCost installation — if found, validates the llm-d configuration, then exits
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
| `--image REPO:TAG` | upstream release | Deploy a specific OpenCost image |

### Non-interactive install

```bash
# Accept GCP default prices
./install-opencost.sh --image "ghcr.io/opencost/opencost:develop-latest@sha256:e0c09b268d8243c45323fffec8ceea1434e9f7e982905af651b1adebfc7e3135" -y

# Supply your own pricing file
./install-opencost.sh --image "ghcr.io/opencost/opencost:develop-latest@sha256:e0c09b268d8243c45323fffec8ceea1434e9f7e982905af651b1adebfc7e3135" --pricing-config /path/to/prices.json

# Install Prometheus and OpenCost together in one namespace
./install-opencost.sh --image "ghcr.io/opencost/opencost:develop-latest@sha256:e0c09b268d8243c45323fffec8ceea1434e9f7e982905af651b1adebfc7e3135" --install-prometheus -n llm-d-monitoring
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
llm_cost_per_million_tokens{model_name="Qwen3-32B",model_version="unknown",namespace="llm-d",cost_basis="allocation",phase="",allocation_method=""} 4.21
llm_total_hourly_cost{model_name="Qwen3-32B",model_version="unknown",namespace="llm-d",cost_basis="allocation"} 0.037
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

# Cost time series, aggregated by model, for the last 24 hours (accumulate is required)
curl "http://localhost:9003/inferenceCost/timeseries?window=24h&aggregate=model_name&accumulate=hour" | jq .
```

For the full API reference — available query parameters, filtering, aggregation options, and response schema — see the [OpenCost REST API Endpoints documentation](https://github.com/opencost/opencost/blob/develop/docs/inference-cost-tracking.md#rest-api-endpoints).

### Run the llm-d config checks

Re-run the installer against an existing OpenCost deployment to validate the llm-d configuration:

```bash
./install-opencost.sh --prometheus-endpoint http://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090
```

The script checks:

1. kube-state-metrics exposes `llm-d.ai/model` in `kube_pod_labels`
2. Shared infrastructure pods carry `llm-d.ai/inference-shared=true`
3. `vllm:prompt_tokens_total` metrics are present in Prometheus
4. The `model_name` label in vLLM metrics matches `llm-d.ai/model` pod labels
5. `INFERENCE_COST_ENABLED=true` is set in the OpenCost pod

## Configuration details

### Inference cost environment variables

The installer configures OpenCost's exporter with the following environment variables:

| Variable | Value | Description |
|---|---|---|
| `INFERENCE_COST_ENABLED` | `true` | Activates the inference cost module |
| `INFERENCE_MODEL_LABEL` | `llm-d.ai/model` | Pod label whose value must exactly match vLLM's `model_name` metric label — used as the join key for cost attribution |
| `INFERENCE_SHARED_INFRA_LABEL` | `llm-d.ai/inference-shared` | Pod label that identifies shared infrastructure pods |
| `INFERENCE_SHARED_INFRA_LABEL_VALUE` | `true` | Expected value of the shared infra label |
| `INFERENCE_COLLECTION_INTERVAL` | `2m` | How often the background collector runs and refreshes the `llm_*` Prometheus gauges |

#### Label usage clarification

OpenCost uses two different labels to track inference costs:

1. **`llm-d.ai/model: <model-name>`** — Applied to model-serving pods (vLLM decode/prefill deployments)
   - Used to attribute costs directly to specific models
   - Example: `llm-d.ai/model: Qwen3-32B`
   - These pods serve specific models and their costs are tracked per-model

2. **`llm-d.ai/inference-shared: "true"`** — Applied to shared infrastructure pods (router/EPP/gateway)
   - Used to identify pods that provide shared services across multiple models
   - These pods don't serve a specific model but support the inference pipeline
   - Their costs are distributed proportionally across the models they serve
   - Example components: llm-d router (EPP), Envoy gateway, request preprocessing services

**Important:** Model-serving pods should have the `model` label, NOT the `inference-shared` label. The `inference-shared` label is only for shared infrastructure components.

### kube-state-metrics label allowlist

The `install-prometheus-grafana.sh` script (when used to install Prometheus for llm-d) automatically configures kube-state-metrics to expose `llm-d.ai/*` pod labels in `kube_pod_labels`. This is required for OpenCost to join allocation costs by model. If you manage Prometheus separately, add the following to your kube-prometheus-stack Helm values:

```yaml
kube-state-metrics:
  metricLabelsAllowlist:
    - pods=[llm-d.ai/role,llm-d.ai/model,llm-d.ai/accelerator-vendor,llm-d.ai/accelerator-variant,llm-d.ai/engine-type,llm-d.ai/inference-serving,llm-d.ai/managed, llm-d.ai/inference-shared]
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
