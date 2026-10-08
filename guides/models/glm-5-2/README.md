# GLM-5.2-FP8 on H200

## Overview

This guide deploys [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) (753B MoE) on H200
nodes, P/D-disaggregated with wide expert parallelism via a `DisaggregatedSet` (a pair of
LeaderWorkerSets). It is optimized for agentic workloads: the configuration follows the analysis
in the
[GLM-5.2 agentic serving blog post](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d),
which sizes each choice against production agentic traces (median main-agent request: ~195K
input tokens and ~317 output tokens; 96% of turns reuse at least 90% of the input from their
best earlier main-agent turn). Each layer addresses a specific pressure of that workload:

- **Wide EP (DEP)** — the MoE experts are spread across ranks (TP=1, DP=EP) instead of
  tensor-parallel sharding, and GLM-5.2's MLA keeps the per-token KV footprint small; together
  they preserve HBM for KV cache, and cache hit rate — not FLOPs — sets throughput here.
- **P/D disaggregation** (NIXL KV transfer) — input processing and token generation use
  separate pools, so each side can use the backend and replica count suited to its work.
  Prefill runs DeepEP high-throughput all-to-all; decode runs low-latency (IBGDA + NVSHMEM).
- **Dual-tier prefix-cache routing** — the EPP scores both GPU- and CPU-resident prefix caches
  when picking an endpoint, so resumed sessions land where their context can be restored rather
  than recomputed.
- **CPU+NVMe tiered KV offloading** — CPU memory keeps evicted prefixes reusable after HBM
  fills. Local NVMe supplies a lower tier when needed.
- **MTP speculative decoding** (3 draft tokens) — cuts median time between output tokens by
  about half on this workload (see [Benchmark Results](#benchmark-results)).

The guide composes the [wide expert parallelism](../../../docs/well-lit-paths/foundations/wide-expert-parallelism.md),
[P/D disaggregation](../../../docs/well-lit-paths/foundations/pd-disaggregation.md) and
[tiered prefix cache](../../tiered-prefix-cache/README.md) foundations. Serving uses the
DeepGemm MoE backend with tool-calling (`glm47`) and reasoning (`glm45`) parsers. Tested on
CoreWeave (CKS) with InfiniBand networking.

## Default Configuration

The default deployment is the latency-leaning Pareto configuration from the blog post:
**`p3w2d1w2`** — 3 DEP16 prefill replicas and 1 DEP16 decode replica, 8 nodes / 64 GPUs —
with MTP and tiered KV offloading enabled.

| Parameter                | Value                                                                        |
| ------------------------ | ---------------------------------------------------------------------------- |
| Model                    | [zai-org/GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8)            |
| Accelerator              | NVIDIA H200 (8 GPUs per node)                                                |
| Serving topology         | P/D disaggregated — 3 prefill replicas + 1 decode replica, each TP=1, DP=16, EP=16 (DEP16, 2 nodes) |
| DP model                 | Supervisor (`--data-parallel-multi-port-external-lb`)                        |
| All-to-all               | `deepep_v2` (prefill), `deepep_low_latency` with IBGDA + NVSHMEM (decode)    |
| MoE backend              | DeepGemm                                                                     |
| KV transfer              | NixlConnector                                                                |
| KV cache offloading      | CPU + NVMe tiered on prefill (`offloading-tiered` component)                 |
| MTP speculative decoding | On (3 draft tokens)                                                          |
| `gpu-memory-utilization` | 0.935 (prefill), 0.95 (decode); 0.80 on multi-node replicas                  |
| Routing                  | Dual GPU+CPU prefix-cache scoring ([`glm-5.2-overrides.values.yaml`](router/glm-5.2-overrides.values.yaml)) |
| Reasoning / tool-call    | glm45 / glm47                                                                |

If your SLO target is throughput rather than TTFT, `p2w2d2w2` (2 prefill + 2 decode, also
64 GPUs) is the second Pareto point measured in the blog post. The full topology range — from
the 2-node `p1w1d1w1` for functional validation up to the 10-node `p3w2d2w2` — is in
[Scaling and Alternative Topologies](#scaling-and-alternative-topologies).

### Routing Modes

This guide supports two prefix-cache routing modes (`ROUTING`), selected with tabs in
[Deploy the llm-d Router](#1-deploy-the-llm-d-router):

- **Approximate prefix routing (`ROUTING=approximate`, default)** — scores GPU- and CPU-resident
  prefix caches heuristically (`glm-5.2-overrides.values.yaml`), paired with the `default`
  (`p3w2d1w2` + tiered offloading) or any raw topology overlay.
- **Precise prefix-cache routing (`ROUTING=precise`, experimental)** — replaces the approximate
  prefix index with exact KV-event-backed routing (`precise-routing.values.yaml`) on the 3-node
  `p2w1d1w1-precise` deployment (24 GPUs, 64-token KV blocks, `wide-ep-render` tokenizer Service,
  and per-rank ZMQ KV-event subscriptions), validated on three 8-GPU H200 nodes with InfiniBand
  on CoreWeave.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA H200 | `gpu` | `zai-org/GLM-5.2-FP8` | 🟡 community | CoreWeave H200 (8 GPUs per node), InfiniBand · default 8 nodes / 64 GPUs (`p3w2d1w2` + tiered KV offloading); 2 to 10 nodes by `DEPLOYMENT` · P/D disaggregated wide-EP; needs an all-to-all RDMA fabric |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- Use a Gateway API Inference Extension bundle >= `v1.5.0-rc.2` (required because the
  `InferencePool` exposes all eight DP rank ports as `targetPorts`). For `ROUTING=precise`, use an
  EPP build with per-rank KV-event attribution
  ([llm-d-router#2233](https://github.com/llm-d/llm-d-router/pull/2233)).

- Deploy the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1`
  or newer. When installing with Helm, pass `--set enableDisaggregatedSet=true` to enable the
  `DisaggregatedSet` validating webhook and RBAC used by the model server.

- Provide H200 nodes on an all-to-all RDMA fabric: DeepEP requires every NIC on a host to reach
  every NIC on all other hosts (rail-only networks fail). See
  [RDMA and Networking Configuration](../../../docs/infrastructure/rdma/README.md) and the
  [multi-node deployment guide](../../../docs/infrastructure/multi-node.md).

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Install the [monitoring stack](../../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d): the manifests, Helm values, and Kustomize overlays it applies live next to this guide. Set the branch and clone the repo (if you already have a checkout, skip this and run the remaining commands from inside it):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

**Set the guide-specific environment variables** (`DEPLOYMENT` picks the recommended `default` overlay or one of the [alternative topologies](#scaling-and-alternative-topologies); `ROUTING=precise` overrides it with `p2w1d1w1-precise` in the router step):

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=glm-5-2
export NAMESPACE=llm-d-glm-5-2
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllm # options: vllm
export ROUTING=approximate # options: approximate, precise
export DEPLOYMENT=default # options: default, p1w1d1w1, p1w1d1w2, p1w2d1w2, p2w1d1w1, p2w1d1w1-precise, p2w1d1w2, p2w2d1w2, p2w2d2w2, p3w2d1w2, p3w2d2w2
export MODEL=zai-org/GLM-5.2-FP8 # the model every deployment serves
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

**Install the Gateway API Inference Extension CRDs:**

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

**Create a target namespace for the installation:**

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

## Installation Instructions

### 1. Deploy the llm-d Router

All 8 DP rank ports (8000-8007) are exposed as `targetPorts` on the `InferencePool` (`glm-5-2.values.yaml`) for per-rank routing. Select the Helm values overrides for your routing mode (`ROUTING`):

<!-- tabs:start group=routing -->
<details open>
<summary><b>Approximate Prefix Routing (Default)</b></summary>

Layers [`glm-5.2-overrides.values.yaml`](router/glm-5.2-overrides.values.yaml) on top of [`glm-5-2.values.yaml`](router/glm-5-2.values.yaml), replacing the single prefix-cache scorer with dual prefix-cache scoring for P/D routing:

- **GPU prefix-cache scorer** (weight 5) — auto-tuned, tracks GPU-resident prefix blocks.
- **CPU prefix-cache scorer** (weight 2) — fixed LRU capacity (200k entries per server), tracks CPU-offloaded prefix blocks.
- **Active-request scorer** (weight 1 prefill, 3 decode) — load balancing.

<!-- guide:deploy.router_values.approximate start -->
```bash
# only when ROUTING=approximate:
# Paths to values files: the wide-EP router values, then the GLM-5.2
# dual GPU+CPU prefix-cache scoring overrides on top
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
export ROUTER_OVERRIDES_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/glm-5.2-overrides.values.yaml"
```
<!-- guide:deploy.router_values.approximate end -->

</details>
<details>
<summary><b>Precise Prefix-Cache Routing (Experimental)</b></summary>

Layers [`precise-routing.values.yaml`](router/precise-routing.values.yaml) on top of [`glm-5-2.values.yaml`](router/glm-5-2.values.yaml), replacing the approximate prefix index with exact KV-event-backed routing (`precise-prefix-cache-producer` + `prefix-cache-affinity-filter` + `token-load-scorer`). It also sets `DEPLOYMENT=p2w1d1w1-precise`, the only overlay that publishes per-rank KV events and deploys the `wide-ep-render` Service.

<!-- guide:deploy.router_values.precise start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when ROUTING=precise:
# Paths to values files: the wide-EP router values, then the GLM-5.2
# precise KV-event-backed prefix-cache routing overrides on top
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
export ROUTER_OVERRIDES_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/precise-routing.values.yaml"
# Precise routing needs the kv-events + render overlay
export DEPLOYMENT=p2w1d1w1-precise
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.router_values.precise end -->

</details>
<!-- tabs:end -->

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -f ${ROUTER_OVERRIDES_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

> [!NOTE]
> The published [benchmarks](#aiperf-command) ran through an Istio Gateway (Gateway Mode with
> `provider.name=istio`) rather than the standalone proxy.

### 2. Deploy the Model Server

**Apply the Kustomize overlay** for your deployment (`DEPLOYMENT=default` deploys `p3w2d1w2` + tiered offloading for approximate routing; `ROUTING=precise` set `DEPLOYMENT=p2w1d1w1-precise` in the router step, which includes per-rank KV-event publishing and the `wide-ep-render` Service):

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}
```
<!-- guide:deploy.modelserver end -->

Two hardware notes: prefill workers request `1500Gi` DRAM for the CPU offload tier, and the
NVMe tier uses a host-path volume at `/mnt/local/kv-cache`. On nodes without local NVMe, swap
the overlay's `offloading-tiered` component for `offloading-cpu` (DRAM only).

Wait for pods to become ready (model load takes time; the startup probe allows up to 45
minutes):

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=GLM-5.2-FP8 -w
```

**(Optional) Deploy the monitoring resources for model servers** (requires the Prometheus
Operator from the monitoring stack mentioned in [Prerequisites](#prerequisites)): PodMonitors
that scrape each DP rank's port (`rank0`-`rank7`). See [Monitoring](#monitoring-optional) for
node-exporter sidecars and the plain-Prometheus alternative.

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/monitoring
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Explain how a simple agent loop works in 3 sentences.\"}"'
```
<!-- guide:verify.tests.request end -->

**Check that the router reaches a model server that serves `MODEL`:** the response must name the model and carry generated tokens:

<!-- guide:verify.tests.served_model start -->
```bash
# The router must reach a model server that serves MODEL and returns tokens
kubectl run serve-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'R=$(curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Say hello.\", \"max_tokens\": 16}")
    echo "${R}" | grep -q "\"model\":\"${MODEL}\"" && echo "${R}" | grep -q "\"choices\"" \
      && echo "OK: ${MODEL} served through the router" \
      || { echo "FAIL: ${R}"; exit 1; }'
```
<!-- guide:verify.tests.served_model end -->

### 3. Verify Precise Prefix-Cache Routing (`ROUTING=precise`)

When running `ROUTING=precise` with `DEPLOYMENT=p2w1d1w1-precise`, verify that the `wide-ep-render` Service returns token IDs and that each engine pod maintains eight active ZMQ subscriptions (`5557`–`5564`) to the EPP:

<!-- guide:verify.tests.precise_routing start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when ROUTING=precise:
# The render Service must return token IDs for MODEL
kubectl run render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://wide-ep-render:8000/v1/completions/render" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"render check\", \"max_tokens\": 1}"'

# only when ROUTING=precise:
# Each engine pod must hold 8 established ZMQ subscriptions (ports 5557-5564) from the EPP
EPP_IP=$(kubectl -n ${NAMESPACE} get endpointslices \
  -l kubernetes.io/service-name=${GUIDE_NAME}-epp \
  -o jsonpath='{.items[0].endpoints[0].addresses[0]}')
ZMQ_FAILED=0
for POD in $(kubectl -n ${NAMESPACE} get pods \
  -l llm-d.ai/inference-serving=true -o name); do
  N=$(kubectl -n ${NAMESPACE} exec "${POD}" -c vllm -- python3 -c "
hx=''.join(f'{int(o):02X}' for o in reversed('${EPP_IP}'.split('.')))
print(sum(1 for line in open('/proc/net/tcp')
          if len(line.split()) > 3 and line.split()[3] == '01'
          and line.split()[2].split(':')[0] == hx
          and 5000 <= int(line.split()[1].split(':')[1], 16) < 6000))")
  if [ "${N:-0}" -ge 8 ]; then echo "OK: ${POD} ${N} subscriptions"
  else echo "FAIL: ${POD} ${N:-0} subscriptions (want 8)"; ZMQ_FAILED=1; fi
done
[ "${ZMQ_FAILED}" = 0 ]
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.precise_routing end -->

Send the same long prompt twice: the second request routes to the same prefill rank, with `vllm:prefix_cache_hits_total` increasing by the prompt length aligned to 64-token blocks.

## Scaling and Alternative Topologies

Every topology below is a Kustomize overlay under `modelserver/gpu/vllm/deployments/`: set
`DEPLOYMENT` to its name and apply it with the [model server step](#2-deploy-the-model-server).

| Deployment | Prefill                    | Decode                        | Nodes / GPUs |
| ---------- | -------------------------- | ----------------------------- | ------------ |
| `p1w1d1w1` | 1 replica, 1 node, DEP8    | 1 replica, 1 node, DEP8      | 2 / 16       |
| `p1w1d1w2` | 1 replica, 1 node, DEP8    | 1 replica, 2 nodes, DEP16    | 3 / 24       |
| `p1w2d1w2` | 1 replica, 2 nodes, DEP16  | 1 replica, 2 nodes, DEP16    | 4 / 32       |
| `p2w1d1w1` | 2 replicas, 1 node, DEP8   | 1 replica, 1 node, DEP8      | 3 / 24       |
| `p2w1d1w1-precise` | 2 replicas, 1 node, DEP8 (`kv-events` + `render`) | 1 replica, 1 node, DEP8 (`kv-events`) | 3 / 24 |
| `p2w1d1w2` | 2 replicas, 1 node, DEP8   | 1 replica, 2 nodes, DEP16    | 4 / 32       |
| `p2w2d1w2` | 2 replicas, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16    | 6 / 48       |
| `p2w2d2w2` | 2 replicas, 2 nodes, DEP16 | 2 replicas, 2 nodes, DEP16   | 8 / 64       |
| `p3w2d1w2` | 3 replicas, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16    | 8 / 64       |
| `p3w2d2w2` | 3 replicas, 2 nodes, DEP16 | 2 replicas, 2 nodes, DEP16   | 10 / 80      |

> [!NOTE]
> The alternatives are to be hardened until the coming llm-d release.

Raw topology overlays ship with MTP on and offloading off; opt in or out per deployment by
adding [Kustomize components](https://kubectl.docs.kubernetes.io/guides/config_management/components/)
from `modelserver/gpu/vllm/components/` to the overlay's `kustomization.yaml` under
`components:` (the default overlay is exactly `p3w2d1w2` + `offloading-tiered`). Component env
entries merge by name, so their values replace the base defaults.

| Component | Targets | Effect |
| --------- | ------- | ------ |
| `offloading-cpu` | prefill only | CPU-only KV cache offloading (`OFFLOADING_MODE=cpu`) |
| `offloading-tiered` | prefill only | CPU + NVMe tiered KV cache offloading (`OFFLOADING_MODE=tiered`) |
| `no-mtp` | prefill + decode | Disables MTP speculative decoding (`ENABLE_MTP=0`) |
| `kv-events` | prefill + decode | KV-event publishing for the [precise prefix-cache routing mode](#1-deploy-the-llm-d-router) |

## Manifest Reference

### MTP Speculative Decoding

On by default (3 tokens) for both prefill and decode. Disable with the `no-mtp` component or
`ENABLE_MTP=0`. Token count: `MTP_NUM_TOKENS` (default `3`).

### KV Cache Offloading (Prefill)

Off in the raw topology overlays; enabled via the `offloading-cpu` or `offloading-tiered`
component.

- **`offloading-cpu`** — CPU-only offloading via `OffloadingConnector`. Uses mmap in
  `/dev/shm`. The pod allocates 1500Gi memory and 1500Gi `dshm` to accommodate 8 DP
  ranks' mmap regions. `cpu_bytes_to_use` is per-rank — total CPU KV cache = value x 8.
- **`offloading-tiered`** — CPU + NVMe tiered offloading via `TieringOffloadingSpec`.
  Same CPU tier as above, plus NVMe as a secondary eviction target. Host-path volume at
  `/mnt/local/kv-cache` mounted as `/mnt/nvme-cache`.

Decode pods do not use offloading (256Gi dshm, 512Gi memory).

### InfiniBand Networking

Both prefill and decode configure IB for multi-node communication:

| Variable                  | Value  | Purpose                                          |
| ------------------------- | ------ | ------------------------------------------------ |
| `NCCL_IB_HCA`            | `ibp`  | Filter IB HCAs for NCCL collectives              |
| `NVSHMEM_HCA_PREFIX`      | `ibp`  | Filter IB HCAs for NVSHMEM (decode low-latency)  |
| `NVSHMEM_REMOTE_TRANSPORT` | `ibgda` | GPUDirect Async for NVSHMEM                     |
| `rdma/ib`                 | `8`    | Request 8 RDMA/IB devices per pod                |

Multi-node deployments (`LWS_GROUP_SIZE > 1`) automatically set `NVSHMEM_SYMMETRIC_SIZE=16G`
and reduce `gpu-memory-utilization` to 0.80 to reserve VRAM for the NVSHMEM heap.

### KV Cache Evictor

`modelserver/gpu/vllm/base/kv-cache-evictor.yaml` deploys a DaemonSet that evicts stale KV
cache data from NVMe when utilization exceeds 90%, targeting 70%.

## Monitoring (Optional)

Node-exporter sidecars on each pod collect InfiniBand, CPU, memory pressure, and network
retransmission metrics. Merge the per-DP-rank Prometheus scrape configs into an existing
`prometheus-server` configmap with:

```bash
NAMESPACE=${NAMESPACE} bash ${REPO_ROOT}/guides/models/${GUIDE_NAME}/monitoring/apply-scrape-configs.sh
```

If you run the Prometheus Operator instead, apply the PodMonitors with `MONITORING=true` in the
[model server step](#2-deploy-the-model-server).

DCGM custom metrics: `modelserver/gpu/vllm/base/dcgm-custom-metrics.yaml`.

## Benchmarking

The results below use requests derived from production agentic traces and replayed with
[aiperf](https://github.com/ai-dynamo/aiperf) on CoreWeave H200 nodes with InfiniBand between
nodes and NVLink within a node. Limited-input-length runs filter aiperf to only use shorter
requests for apples-to-apples comparisons with EP8 deployments that cannot handle the entire
dataset due to some entries containing close to 1M tokens. See the
[blog post](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d) for the full
analysis and figures. An `inference-perf` preset via
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) is not yet published for this
deployment.

For a quick synthetic throughput run, [`inference-perf.yaml`](inference-perf.yaml) runs an
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) job (2K input / 2K output
tokens, 2048 concurrent requests, 8192 total) against the standalone `glm-5-2-epp` Service:

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/inference-perf.yaml
```

### Benchmark Overlays

Pre-built overlays under `modelserver/gpu/vllm/deployments/benchmark/<config>/<topology>/`
combine components with topology patches. Each matches a tested configuration on CoreWeave H200:

| Configuration | Directory | Components |
| ------------- | --------- | ---------- |
| Baseline | `benchmark/baseline/` | `no-mtp` |
| MTP + Offloading | `benchmark/mtp-offloading/` | `offloading-tiered` |
| Offloading | `benchmark/offloading/` | `no-mtp` + `offloading-tiered` |
| Full ISL + MTP + Offloading | `benchmark/full-isl-mtp-offloading/` | `offloading-tiered` |

Deploy a benchmark config:

```bash
kubectl apply -n ${NAMESPACE} \
    -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/gpu/vllm/deployments/benchmark/<config>/<topology>
```

Example overlay (`mtp-offloading/p1w1d1w1`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../providers/coreweave
components:
  - ../../../../components/offloading-tiered
patches:
  - target:
      group: disaggregatedset.x-k8s.io
      kind: DisaggregatedSet
    patch: |-
      # Roles are ordered [prefill, decode] in the base DisaggregatedSet.
      - op: replace
        path: /spec/roles/0/spec/replicas
        value: 1
      - op: replace
        path: /spec/roles/0/spec/leaderWorkerTemplate/size
        value: 1
      - op: replace
        path: /spec/roles/1/spec/replicas
        value: 1
      - op: replace
        path: /spec/roles/1/spec/leaderWorkerTemplate/size
        value: 1
```

### aiperf Command

Every reported number comes from the same [aiperf](https://github.com/ai-dynamo/aiperf) `profile`
invocation, run from inside the cluster against the Kubernetes Gateway Service
(`llm-d-inference-gateway-istio`, from a Gateway Mode router installation with
`provider.name=istio`; see the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router))
and swept across concurrency. Against the standalone proxy, use `http://${GUIDE_NAME}-epp:80/v1`
instead. The dataset used is the
`semianalysis_cc_traces_weka_with_subagents` aiperf preset, backed by
[`semianalysisai/cc-traces-weka-062126`](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126)
on HuggingFace.

```bash
aiperf profile \
    --scenario 'inferencex-agentx-mvp' \
    --url 'http://llm-d-inference-gateway-istio:80/v1' \
    --model 'zai-org/GLM-5.2-FP8' \
    --max-context-length <142000|10000000> \
    --endpoint-type 'chat' \
    --streaming \
    --use-server-token-count \
    --public-dataset 'semianalysis_cc_traces_weka_with_subagents' \
    --concurrency <16|32|64|128|256|512> \
    --random-seed 42 \
    --benchmark-duration 900 \
    --server-metrics 'http://llm-d-inference-gateway-istio:80/metrics' \
    --no-gpu-telemetry \
    --output-artifact-dir <path> \
    --ui 'simple'
```

Only `--max-context-length` and the `--concurrency` sweep differ across the four reported
configurations:

| Configuration | `--max-context-length` | `--concurrency` sweep |
| --------------------------- | ----------------------- | ------------------------- |
| Baseline | `142000` | 16, 32, 64, 128 |
| MTP + Offloading | `142000` | 16, 32, 64, 128 |
| Offloading | `142000` | 16, 32, 64, 128 |
| Full ISL + MTP + Offloading | `10000000` | 16, 32, 64, 128, 256, 512 |

`142000` truncates the trace dataset to requests that fit an EP8 (1-node) prefill deployment.
`10000000` is effectively unbounded and replays full traces (up to ~1M input tokens) — this is
the "Full ISL" config. At concurrency 256/512, some Full ISL runs on smaller topologies exceeded
server capacity (warmup failures) and are excluded from the reported results.

## Benchmark Results

### Recommended 64-GPU Deployment

The full-input-length reference uses `p3w2d1w2`, native MTP, CPU KV offloading, and dual
GPU+CPU prefix-cache routing:

| Concurrency | Requests | TTFT p50 / p90 | ITL p50 / p90 | Turn completion p50 | Output throughput |
| ----------- | -------- | --------------- | --------------- | ------------------- | ----------------- |
| 64 | 3,543 | 1.71 s / 6.36 s | 15.5 ms / 18.9 ms | 9.6 s | 3,679 tok/s |
| 128 | 6,242 | 2.18 s / 15.82 s | 19.1 ms / 23.1 ms | 14.0 s | 6,281 tok/s |

### What Each Optimization Changes

- **CPU offloading keeps evicted prefixes reusable.** In matched 24-GPU runs at concurrency
  32, offloading raises output 13%, cuts median TTFT 32%, and cuts median turn completion 27%,
  while median ITL is unchanged. At concurrency 128, baseline output remains near 951 tok/s
  while offloading reaches 2,776 tok/s.
- **MTP reduces decode work.** On matched 32-GPU `p1w2d1w2` deployments with CPU offloading
  enabled in both runs, MTP raises aggregate output 1.6-2.0x and average output per user
  1.6-1.8x through concurrency 128.
- **Prefill and decode replicas change different parts of the turn.** At concurrency 128,
  `p2w2d2w2` produces 8% more output and completes the median turn 10% sooner than
  `p3w2d1w2` at the same 64-GPU budget. `p3w2d1w2` reaches the first token 19% sooner and
  restores 63% less KV from CPU memory.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
