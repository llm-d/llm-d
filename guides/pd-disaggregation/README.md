# P/D Disaggregation

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml)
[![E2E (AMD ROCM MORI)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-x.yaml)

## Overview

This guide deploys `openai/gpt-oss-120b` with prefill-decode disaggregation, improving throughput per GPU and quality of service. Since disaggregation is natively built into llm-d Router, we can compose features like prefix- and load-aware routing with disaggregated serving. In this example, we will demonstrate a deployment with:

* 8 TP=1 Prefill Instances
* 2 TP=4 Decode Instances

This guide also has two alternate variants:

* **[Google TPU](./README.tpu.md)** — the same P/D pattern on GKE TPU (v6e & v7x).
* **[DisaggregatedSet](./README.ds.md)** — the same deployment managed as a single LWS `DisaggregatedSet` resource, with coordinated P/D rollouts, `slices` for replicating the whole topology into independent copies, and per-domain placement policy.

### P/D Best Practices

P/D disaggregation provides more flexibility in navigating the trade-off between throughput and interactivity([ref](https://arxiv.org/html/2506.05508v1)).
In particular, due to the elimination of prefill interference to the decode phase, P/D disaggregation can achieve lower inter token latency (ITL), thus
improving interactivity. For a given ITL goal, P/D disaggregation can benefit overall throughput by:

* Specializing P and D workers for compute-bound vs latency-bound workloads
* Reducing the number of copies of the model (increasing KV cache RAM) with wide parallelism

However, P/D disaggregation is not a target for all workloads. We suggest exploring P/D disaggregation for workloads with:

* Medium-large models (e.g. gpt-oss-120b)
* Longer input sequence lengths (e.g 10k ISL | 1k OSL, not 200 ISL | 200 OSL)
* Sparse MoE architectures with opportunities for wide-ep

As a result, as you tune your P/D deployments, we suggest focusing on the following parameters:

* **Heterogeneous Parallelism**: deploy P workers with less parallelism and more replicas and D workers with more parallelism and fewer replicas, see the TP ratio warning below.
* **xPyD Ratios**: tuning the ratio of P workers to D workers to ensure balance for your ISL|OSL ratio

> [!WARNING]
> The NixlConnector has known issues and limitations around TP ratio direction and stale agent caching after prefill pod restarts. See [Known NIXL Connector Issues and Limitations](../../docs/operations/disaggregation/vllm.md#known-nixl-connector-issues-and-limitations) for details.

### Supported Hardware Backends

This guide includes configuration for the following accelerators:

| Backend             | Directory                  | Notes                                                    |
| ------------------- | -------------------------- | -------------------------------------------------------- |
| NVIDIA GPU (vLLM)   | `modelserver/gpu/vllm/`    | vLLM, tested nightly on GKE (see [Cluster Pre-provisioning](#gke-cluster-pre-provisioning-with-dra--rdmaroce)) |
| NVIDIA GPU (vLLM + DisaggregatedSet) | `modelserver/gpu/vllm-ds/` | Manages the whole P/D topology as one LWS `DisaggregatedSet` with `slices`, see [DisaggregatedSet Guide](./README.ds.md) |
| NVIDIA GPU (SGLang) | `modelserver/gpu/sglang/`  | SGLang, validated each release                           |
| Google TPU          | `modelserver/tpu/v6/vllm/` & `modelserver/tpu/v7/vllm/` | GKE TPU (v6e & v7x), see [TPU Guide](./README.tpu.md) |
| AMD GPU             | `modelserver/amd/vllm/`    | AMD GPU, community contributed                           |
| Intel XPU           | `modelserver/xpu/vllm/`    | Intel Data Center GPU Max 1550+, community contributed   |
| Intel XPU + RDMA    | `modelserver/xpu/vllm-rdma/` | Intel XPU with RDMA via UCX (`ib,rc,ze_copy`), requires RDMA DRA driver |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks. These configurations are maintained by their respective hardware vendors and are not guaranteed as production-ready examples. Users deploying on non-default hardware should review and adjust the configurations for their environment.

### Supported KV Transfer Backends

P/D disaggregation requires a KV transfer backend to move KV cache blocks from prefill workers to decode workers. The transfer backend is configured via vLLM's `--kv-transfer-config` flag.

> [!NOTE]
> The following table represents vLLM's KVTransfer compatibility. SGLang also supports these KVTransfer backends but its implementation will look different and is coming soon.

| Connector | Overlay | Transport | Notes |
| --------- | ------- | --------- | ----- |
| NixlConnector | `base`, `coreweave`, `gke` | UCX (RDMA / TCP) | Default. Supports heterogeneous TP across P/D. |
| MooncakeConnector | `cks-mooncake` | RDMA via Mooncake Transfer Engine | Requires same TP on prefill and decode. CKS with InfiniBand. |

The `base` overlay uses NixlConnector and works on most clusters. Alternative overlays swap the connector and add infrastructure-specific configuration (e.g., RDMA device requests).

<details>
<summary><b>MooncakeConnector details</b></summary>

The `cks-mooncake` overlay uses [Mooncake Transfer Engine](https://github.com/kvcache-ai/Mooncake) as the KV transfer backend. Mooncake provides point-to-point RDMA-based transfer of KV cache blocks between prefill and decode workers. It is used here as a transport layer — llm-d remains responsible for routing, orchestration, and scheduling.

> [!IMPORTANT]
> This overlay configures `MooncakeConnector` for P/D KV transfer only. It does **not** configure Mooncake Store (`MooncakeStoreConnector`), which provides distributed KV storage for tiered cache offloading and is a separate integration.

**Constraints:**
- MooncakeConnector requires the **same `tensor-parallel-size`** on both prefill and decode instances. The `cks-mooncake` overlay uses TP=1 for both. If your model requires higher TP, set both sides to the same value and adjust GPU resource requests.
- `mooncake-transfer-engine` must be installed in the vLLM container image. The standard `vllm/vllm-openai` image may not include it — you may need a custom image. See the [Mooncake installation docs](https://kvcache-ai.github.io/Mooncake/).

**CKS / RDMA prerequisites:**
- NVIDIA GPU Operator (or equivalent) with GPUs visible to pods via `nvidia.com/gpu`.
- InfiniBand / RDMA devices available on worker nodes and exposed **inside pods** (host-level RDMA alone is not sufficient).
- RDMA device plugin exposing `rdma/ib` resources. Typically provided by the NVIDIA Network Operator, Multus with SR-IOV, or your CKS provider's equivalent.

Validate RDMA from inside a test pod before deploying:

```bash
kubectl run rdma-test --rm -it \
    --image=mellanox/rping-test \
    --overrides='{"spec":{"containers":[{"name":"rdma-test","image":"mellanox/rping-test","command":["ibv_devinfo"],"resources":{"limits":{"rdma/ib":"1"}}}]}}' \
    -- ibv_devinfo
```

**Request flow:**

```
client request → llm-d routing → vLLM prefill (kv_producer) → MooncakeConnector (RDMA) → vLLM decode (kv_consumer) → response
```

**kv-transfer-config reference:**

| Field | Prefill | Decode | Description |
| :---- | :------ | :----- | :---------- |
| `kv_connector` | `MooncakeConnector` | `MooncakeConnector` | Selects Mooncake Transfer Engine |
| `kv_role` | `kv_producer` | `kv_consumer` | Prefill produces KV blocks, decode consumes |
| `kv_connector_extra_config.mooncake_protocol` | `rdma` | `rdma` | Transport protocol |

**Environment variables:**

| Variable | Default | Description |
| :------- | :------ | :---------- |
| `VLLM_MOONCAKE_BOOTSTRAP_PORT` | `8998` | Mooncake bootstrap server port. Must be unique per instance if co-located. |
| `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` | `480` | Seconds before a prefiller releases KV cache if the decoder does not acknowledge. |

**Troubleshooting:**
- **No RDMA in pod**: Check that `rdma/ib` appears in `kubectl describe node` allocatable resources and that the RDMA device plugin is running.
- **MooncakeConnector fails to init**: Verify `mooncake-transfer-engine` is installed (`pip show mooncake-transfer-engine` inside the pod) and the bootstrap port is free.
- **KV transfer failures**: Confirm prefill and decode use the same TP size and can reach each other over the RDMA network.

</details>

## Prerequisites

### GKE: Cluster Pre-provisioning (with DRA & RDMA/RoCE)

Before running this guide, make sure your cluster is configured correctly.

GPU DRA is not yet fully managed by GKE and requires manual node label configuration and driver installation. In addition, you must enable managed **DRANET** (network DRA) for high-performance RoCE networking.

> [!IMPORTANT]
> The current recipe targets the **GKE A3/A4** platform. The **DRANet** (network DRA) setup requires support for both **Hairpin** (direct loopback transfer on the same node) and **Cross-rail** (inter-node multi-rail transfers) routing to ensure proper KV cache exchange between Prefill and Decode nodes.

To create the cluster, node pool, and install the required GPU DRA / network DRA drivers, follow the step-by-step instructions in the [GKE Infrastructure Guide](../../docs/infra-providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke).

### Checkout Repo & Setups

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

```bash
export branch="main" # branch, tag, or commit hash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
```
* Set the following environment variables:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export GUIDE_NAME="pd-disaggregation"
export NAMESPACE="llm-d-pd-disaggregation"
export MODEL_NAME="openai/gpt-oss-120b"
```
* Install the Gateway API Inference Extension CRDs:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
* Create a target namespace for the installation

```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.
<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To employ a Kubernetes Gateway managed proxy instead of the standalone one, then instead of applying the standalone helm chart above, do the following:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster, all guides can share one Gateway each with a separate HTTPRoute.
2. *Deploy the llm-d Router and an HTTPRoute*. The following deploys the llm-d Router with an HttpRoute that connects it to the Gateway created in the previous step (set `provider.name` to the gateway provider you deployed):

```bash
export PROVIDER_NAME=gke # other na, agentgateway or istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART}  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend (defaulting to NVIDIA GPU / vLLM):

#### GPU

Choose the overlay matching your infrastructure provider:
- **GKE**: Deploys on GKE using Dynamic Resource Allocation (DRA) and DRANet (RoCE) as the default high-performance path. Ensure the cluster is configured accordingly (see [Cluster Pre-provisioning](#gke-cluster-pre-provisioning-with-dra--rdmaroce)).
- **CoreWeave**: Deploys on CoreWeave.

> [!TIP]
> Check subdirectories under your provider folder (e.g. `modelserver/gpu/vllm/gke/a4x` and `modelserver/gpu/vllm/gke/a4xmax` for GKE A4X/A4X Max / GB200/GB300 platforms) for platform-specific overlays. If your target hardware has specialized driver, memory, or network interconnect requirements, default provider settings may not adapt to your platform, and you should select the corresponding platform sub-overlay (for example, `export INFRA_PROVIDER=gke/a4xmax`).

```bash
export INFRA_PROVIDER=base # base | coreweave | gke/base | gke/a4x | gke/a4xmax | aws

kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

> [!TIP]
> The `cks-mooncake` overlay uses MooncakeConnector instead of NixlConnector for KV transfer. It targets CKS clusters with InfiniBand / RDMA and has additional prerequisites — see [KV Transfer Backends](#kv-transfer-backends) above.

<details>
<summary><h4>Deploying with SGLang</h4></summary>

To run the disaggregated deployment with SGLang instead of vLLM, apply the SGLang overlay (available for NVIDIA GPU with `base`, `coreweave`, and `gke` infra providers):

```bash
export INFRA_PROVIDER=base # base | coreweave | gke | cks-mooncake

kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/${INFRA_PROVIDER}
```

SGLang-specific notes:

* **Engine flags**: prefill and decode pods launch with `--disaggregation-mode={prefill,decode}` and `--disaggregation-transfer-backend=nixl`. The decode pod's routing-proxy sidecar is configured with `--kv-connector=sglang`.
* **Bootstrap server**: each prefill instance runs a bootstrap server on port `8998` (the default). To use a different port, set `SGLANG_BOOTSTRAP_PORT` on the sidecar and `--disaggregation-bootstrap-port` on the SGLang engine so the two match. P/D peers discover each other through this server rather than vLLM's peer-to-peer negotiation; the KV transfer itself still runs directly over NIXL/RDMA.
* **Operations**: scale up/down, request cancellation, fault tolerance, and rollout behavior differ from vLLM. See [Disaggregated Serving: Operations (SGLang)](../../docs/operations/disaggregation/sglang.md).

</details>

> [!NOTE]
> **Feature parity and known limitations (SGLang vs vLLM)**
>
> * Disaggregation lives in the llm-d Router (EPP) and is engine-agnostic, so SGLang P/D composes with the same prefix-cache-aware and load-aware routing as vLLM.
> * SGLang P/D is **validated each release** on NVIDIA GPU but is not yet part of the nightly E2E CI that covers the vLLM path (the badges above).
> * The SGLang P/D overlays are **NVIDIA GPU only** today; the AMD overlay (`modelserver/amd/vllm/`) provides vLLM P/D only.
> * On the NIXL transfer backend, SGLang has no explicit prefill-side free-notification (as vLLM does) and no prefill-side reclaim timeout, so a request cancelled before the decode initiates the transfer can strand KV cache on the prefill until the pod restarts. See the [SGLang operations doc](../../docs/operations/disaggregation/sglang.md).

### 3. Enable Monitoring (optional)

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).
* Deploy the monitoring resources for model servers:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
```

### 4. Observability & Troubleshooting

Once monitoring is enabled, use the signals below to operate P/D disaggregation. This section covers the metrics that matter **for this path** and how to read them; full metric definitions live in the [metric reference](../../docs/operations/observability/metrics.md) and ready-to-run queries in the [PromQL reference](../../docs/operations/observability/promql.md).

In a P/D deployment the prefill and decode pools scale and fail independently, and every decode step depends on a KV transfer from a prefill worker over NIXL. Most problems show up as an **imbalance between the two pools** or as **KV-transfer stalls**, so watch them as a pair rather than as a single aggregate.

#### Key metrics for this path

| Signal | Why it matters for P/D | Where to look |
|--------|------------------------|---------------|
| Prefill worker utilization (`vllm:num_requests_running{pod=~".*prefill.*"}`) | Prefill is short and bursty. Sustained saturation here means prompts queue before decode can even start, inflating TTFT | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| Decode KV cache utilization (`vllm:kv_cache_usage_perc{pod=~".*decode.*"}`) | Decode holds KV for the full generation. Above ~0.9 the decode pool preempts or rejects, so decode — not prefill — is usually the scaling bottleneck | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| P/D decision ratio (`llm_d_epp_pd_decision_total`) | Confirms the EPP is actually splitting prefill and decode. A ratio drifting toward 0 means requests are falling back to aggregated serving | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| EPP scheduler e2e latency (`llm_d_epp_scheduler_e2e_duration_seconds`) | Rising scheduler latency with healthy pools points at the routing layer, not the model servers | [PromQL → Tier 1](../../docs/operations/observability/promql.md) |
| TTFT vs. ITL split (`vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`) | TTFT regressions localize to prefill or KV transfer; ITL regressions localize to decode. Splitting them tells you which pool to investigate | [Metrics → vLLM](../../docs/operations/observability/metrics.md#key-vllm-metrics) |

> SGLang deployments expose the equivalent signals under `sglang_*` (`sglang_num_running_reqs`, `sglang_token_usage`); the PromQL reference lists both.

#### Common failure modes

* **TTFT regression, decode healthy** — prefill pool is saturated or KV transfer is stalling. Check prefill utilization and TTFT together; if prefill is idle but TTFT is high, suspect NIXL transfer (see the [SGLang operations doc](../../docs/operations/disaggregation/sglang.md) for the prefill-side KV-strand caveat).
* **ITL regression, prefill healthy** — decode pool is the bottleneck. Check decode KV cache utilization; sustained values near 1.0 mean the decode `Deployment` needs more replicas or a larger TP degree.
* **Both pools underutilized but latency high** — routing problem. Check the P/D decision ratio and EPP scheduler e2e latency before touching the model servers.

For alert rules covering these signals, see [Alerting](../../docs/operations/observability/alerting.md).

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "openai/gpt-oss-120b",
        "prompt": "How are you today?"
    }' | jq
```

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a synthetic random-data workload (typical of variable-length prompts in P/D-disaggregated serving) against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **pd-disaggregation-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to exercise the prefill-decode disaggregation pattern under realistic load — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

### 1. Install the `llmdbenchmark` CLI

Automatically clone the benchmark repository into `./llm-d-benchmark/` and create a virtualenv at `./llm-d-benchmark/.venv/` containing dependencies and its installation:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
```

Activate the `venv` and enter the repository directory - both are required: the `venv` puts `llmdbenchmark` on your PATH, and the repository directory contains the `workload/profiles/` and `config/specification/` files that orchestrate the benchmark:

```bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```

> [!NOTE]
> Subsequent `llmdbenchmark` commands in this section assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the two commands above.

### 2. Resolve the endpoint of the stack you just deployed

Set two variables so the rest of the section is topology-agnostic: the endpoint URL and the gateway class. The gateway class tells the CLI which deployment topology the cluster is actually running, without this, the CLI re-renders against the benchmark scenario's default values.

**Standalone Mode** (the default in this guide — no Kubernetes Gateway, EPP pod with an Envoy sidecar):

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly # standalone mode
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"

# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

</details>

### 3. Run the benchmark profile for P/D Disaggregation

`guide_pd-disaggregation_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the saturation load used to generate the [graphs at the bottom of this guide](#benchmarking-report) (constant rate=45 with 45 workers and per-worker concurrency=100) and is shaped to highlight the strengths of the prefill-decode disaggregation pattern under load.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/pd-disaggregation \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_pd-disaggregation_1.yaml \
    --analyze
```

A second profile `guide_pd-disaggregation_2.yaml` is also available for low-rate latency characterization (rate=1, num_workers=100) — pass it instead of `guide_pd-disaggregation_1.yaml` for that mode.

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

<details>
<summary><h4>Cleanup for SGLang</h4></summary>

If you deployed the SGLang overlay, delete that path instead of the vLLM one:

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/${INFRA_PROVIDER}
```

</details>

## Benchmarking Report

The benchmark runs on 16 H200 GPUs on GKE A3 Ultra (`a3-ultragpu-8g`, DRANet RoCE via DRA), with the router's token-based P/D scheduling profiles (`prefix-cache-affinity-filter` + `token-load-scorer` on prefill, `active-request-scorer` on decode) and the calibrated `peakPrefillThroughput: 33821`.

There is a report for each stage.

<details>
<summary><b><i>Click</i></b> here to view the report for `rate=45` from the above example</summary>

```yaml
metrics:
  latency:
    inter_token_latency:
      max: 2.2948232810012996
      mean: 0.0048874225546436485
      min: 0.0
      p0p1: 0.0
      p1: 0.0
      p10: 0.0
      p25: 8.458038792014122e-06
      p5: 0.0
      p50: 1.2325122952461243e-05
      p75: 1.4679040759801865e-05
      p90: 1.8372898921370506e-05
      p95: 3.048051148653026e-05
      p99: 0.010211549471132394
      p99p9: 0.9875524381587752
      units: s/token
    normalized_time_per_output_token:
      max: 0.0720405302838319
      mean: 0.011272806548055027
      min: 0.00045122518049730886
      p0p1: 0.0005145006946235203
      p1: 0.00799626310609281
      p10: 0.009567618076965599
      p25: 0.010268639817630359
      p5: 0.009101943364366889
      p50: 0.011208222057435203
      p75: 0.012235897581742029
      p90: 0.013212064575388663
      p95: 0.013746295733422583
      p99: 0.015525384459981965
      p99p9: 0.02169257163775991
      units: s/token
    request_latency:
      max: 5.500042774016038
      mean: 2.775478398489283
      min: 1.6934288579504937
      p0p1: 1.9034744882385712
      p1: 2.0955666662193835
      p10: 2.3753150632372124
      p25: 2.506768883089535
      p5: 2.270708685088903
      p50: 2.727781204506755
      p75: 3.000033031741623
      p90: 3.205472262110561
      p95: 3.378960671555251
      p99: 3.700869863124099
      p99p9: 5.063714611682857
      units: s
    time_per_output_token:
      max: 0.0315695862618408
      mean: 0.005698083638427052
      min: 1.776400782472017e-05
      p0p1: 2.3671881866074022e-05
      p1: 0.0003802231368589049
      p10: 0.0036351872850322593
      p25: 0.004326083754862285
      p5: 0.0032494267610573917
      p50: 0.00569766377949618
      p75: 0.006938906643558673
      p90: 0.007838766581131792
      p95: 0.00852698527638066
      p99: 0.010109478284085944
      p99p9: 0.012536769559875672
      units: s/token
    time_to_first_token:
      max: 4.202539925929159
      mean: 1.1568768169119075
      min: 0.20732805598527193
      p0p1: 0.22617485262500123
      p1: 0.37198703180765735
      p10: 0.7970825189258903
      p25: 0.927698435029015
      p5: 0.6972236932837405
      p50: 1.1084336100611836
      p75: 1.3245190941961482
      p90: 1.5871762204682462
      p95: 1.7029315309715458
      p99: 2.0101042066374815
      p99p9: 2.972206295224074
      units: s
  requests:
    failures: 0
    # input_length / output_length histograms omitted for brevity
    total: 5400
  throughput:
    output_tokens_per_sec: 12035.852482673554
    requests_per_sec: 42.58097793003138
    total_tokens_per_sec: 224940.74213283046
  time:
    duration: 126.81719073886052
scenario:
  load:
    stages:
    - duration: 120
      rate: 45.0
    num_workers: 45
    type: constant
    worker_max_concurrency: 100
  data:
    type: random
    input_distribution: {mean: 5000, min: 5000, max: 5000}
    output_distribution: {mean: 250, min: 250, max: 250}
  server:
    model_name: openai/gpt-oss-120b
    type: vllm
version: '0.1'
```

</details>

## Comparing llm-d P/D disaggregation to a k8s service

The following scripts run the same benchmark against a standard deployment and service running `openai/gpt-oss-120b`.

#### Run Baseline (Aggregated)

* Deploy (16 replicas of TP=1, with a standard k8s service)

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/pd-disaggregation/baseline/manifest.yaml
```

* Benchmark (using the same workload profile as the main run, but pointed at the baseline service rather than the EPP):

```bash
export ENDPOINT_URL="http://$(kubectl get service baseline -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"

llmdbenchmark \
    --spec           guides/pd-disaggregation \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_pd-disaggregation_1.yaml \
    --workspace      ./results-baseline \
    --analyze
```

(Drives the same `guide_pd-disaggregation_1.yaml` workload — rate=45 for 120s, 45 workers — against the aggregated baseline so the two result sets are directly comparable.)

For this workload (20:1 ISL:OSL, 45 QPS), llm-d disaggregation improved mean E2E latency by ~59% and P95 by ~67%!

| Metric                   | aggregated | llm-d        | Δ% |
| :----------------------- | :--------- | :----------- | :------- |
| **E2E Latency (Mean)**   | **6.7s**   | **2.78s**    | **-59%** |
| **E2E Latency (P95)**    | **10.2s**  | **3.38s**    | **-67%** |
| ITL (Mean)               | 25ms       | 4.9ms        | -80%     |
| ITL (P95)                | 197ms      | <1ms         | -99%     |
| TTFT (Mean)              | 532ms      | 1157ms       | +117%    |
| TTFT (P95)               | 1574ms     | 1703ms       | +8%      |

> [!NOTE]
> The llm-d column is measured with the token-based routing configuration this guide now
> ships (16 × H200, GKE A3 Ultra — the `rate=45` report above). The aggregated column is
> carried over from the original baseline measurement. Chunked streaming delivers several
> tokens per event, so 95% of inter-token gaps measure below 1 ms.

> [!NOTE]
> In aggregated setup, vLLM allocates all GPU resources to
> processing prefills as they arrive. TTFT is elevated in the
> disaggregated setup because less resources are allocated to
> processing prefills.
