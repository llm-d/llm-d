# Wide Expert Parallelism

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-ibm-acc-gpu-vllm-x.yaml)
[![E2E (AMD GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-amd-ci-acc-rocm-vllm-moriio.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-amd-ci-acc-rocm-vllm-moriio.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-intel-acc-xpu-vllm-x.yaml)

## Overview

This guide deploys a large Mixture-of-Experts (MoE) model in a wide expert-parallel (DP/EP) pattern across nodes, with vLLM prefill/decode (P/D) disaggregation and DP-aware routing by the llm-d Router. The NVIDIA GPU and AMD configurations deploy a single [`DisaggregatedSet`](https://lws.sigs.k8s.io/) that manages the prefill and decode `LeaderWorkerSet`s as one versioned unit; the Intel XPU configuration uses two plain `LeaderWorkerSet`s.

Each accelerator serves one model, chosen to teach the mechanics: multi-node `LeaderWorkerSet`/`DisaggregatedSet` deployment, DP/EP configuration in vLLM, and routing to individual DP ranks. For state-of-the-art, benchmarked MoE recipes, see the Models guides, such as [DeepSeek-V4](../models/deepseek-v4/README.md) and [GLM-5.2](../models/glm-5-2/README.md).

### Why wide expert parallelism

Very large MoE models like DeepSeek-R1 can consume 500 GB+ of memory just to hold the weights of the model, pressuring KV cache space for long context and high throughput serving. This problem is especially magnified for models with MLA attention, which replicates the KV cache when sharded with tensor parallelism.

To address these issues, model servers support DP/EP deployments, which deploy the attention layers with data parallelism and the MLP layers with expert parallelism. This deployment pattern scales the KV cache space, as it:

- **Scales to multiple nodes**: the collective operations (dispatch/combine) are sparse, since tokens are only sent to the expert rank after routing, so they consume much less bandwidth than the all-reduces of TP setups. This makes them suitable to run over slower interconnects (InfiniBand, RoCE rather than NVLink).
- **Avoids KV replication**: attention is data-parallel (TP=1 in every DP group), so there is only one copy of each token's KV.

The following visualizes the forward pass in a DP/EP deployment in vLLM:

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../../docs/assets/dp-ep-deployment.svg" alt="DP/EP deployment">
  </picture>
</p>

1. Each rank runs attention independently.
2. The MoE router selects the `topk` experts for each token. This is sparse: in the case of DeepSeek, 8 out of 256 experts are selected.
3. Tokens are "dispatched" (using the `topk_id`) to the proper expert rank (e.g. the green token on rank 1 is routed to E1 and E3).
4. Each expert runs independently.
5. Tokens are "combined" back to the original attention rank.

### Architecture

Multi-node wide-EP deployments are typically combined with disaggregated serving because:

- Disaggregation avoids "bubbles" where rank N is computing a prefill and rank M is computing a decode.
- Specialized kernels for prefill and decode can be used (e.g. DeepEP high-throughput vs. DeepEP low-latency).

As a result, this guide uses:

- Disaggregated prefill and decode, scheduled by the llm-d Router.
- A `DisaggregatedSet` (or `LeaderWorkerSet`s) to manage the multi-node pod groups of vLLM.
- DP/EP configuration in vLLM, with every DP rank exposed on its own port so the router can pick a rank, not just a pod.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../../docs/assets/wide-ep.svg" alt="Multi-Node Wide Expert Parallelism">
  </picture>
</p>

The request flow works as follows:

1. A request arrives at the proxy, which forwards it to the router (EPP).
2. The router schedules the request with P/D disaggregation, using the pod labels to detect the prefill and decode roles, and picks specific DP ranks within the pod groups.
3. The request is routed to the decode pod's sidecar, which forwards it to the selected prefill rank.
4. The prefill rank processes the prompt, executing the forward pass with DP/EP; the all-to-all backend (DeepEP, MoRI) executes the cross-node dispatch/combine collectives. vLLM returns metadata about how to retrieve the KV blocks.
5. The decode rank pulls the KV cache over RDMA (InfiniBand, RoCE, EFA) with the KV connector (NIXL, MoRI-IO).
6. The decode rank generates the output tokens, executing the forward passes with DP/EP.

For more details, see the [P/D architecture](../../docs/architecture/advanced/disaggregation/README.md) and the vLLM docs on [DP deployment](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/), [EP deployment](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/), and [DeepEP and DeepGEMM](https://docs.vllm.ai/en/latest/design/fused_moe_modular_kernel/).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves exactly one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `deepseek-ai/DeepSeek-R1-0528` | ✅ validated | Default. 32× H200 · `DisaggregatedSet`: prefill DP16 + decode DP16, 2 nodes each · DeepEP + NIXL · `INFRA_PROVIDER`: `base`, `gke`, `coreweave` |
| AMD GPU | `amd` | `deepseek-ai/DeepSeek-V3` | ✅ validated | 32× MI355X · `DisaggregatedSet`: prefill DP16 + decode DP16, 2 nodes each · MoRI-EP + MoRI-IO · `INFRA_PROVIDER`: `base`, `amd-ci` |
| Intel XPU | `xpu` | `deepseek-ai/DeepSeek-V2-Lite-Chat` | ✅ validated | 4 XPUs via DRA · `LeaderWorkerSet`: prefill TP2 + decode TP2 with EP · NIXL · `INFRA_PROVIDER`: `base` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

**NVIDIA GPU** (DeepSeek-R1-0528): prefill DP16 and decode DP16 on 32 H200, with DeepEP (high-throughput prefill, low-latency decode) and NIXL KV transfer. Validated on a 32×H200 cluster with InfiniBand networking (`coreweave`) and a 32×H200 cluster on GKE with RoCE networking (`gke`); `base` is the provider-neutral overlay the others patch.

> [!NOTE]
> The NVIDIA GPU configuration uses a custom vLLM image built by llm-d to solve two issues: an NVSHMEM bug on RoCE impacting DeepEP high-throughput mode (llm-d vendors a custom patch), and a vLLM v0.23.0-v0.24.0 bug with the DP supervisor. We plan to migrate to the upstream vLLM images in an upcoming release.

**AMD GPU** (DeepSeek-V3): prefill and decode each run 1 replica of 2 nodes, DP16 (TP=1), on 32 MI355X, with the `mori_high_throughput` all-to-all backend and MoRI-IO KV transfer. `amd-ci` adapts `base` to the AMD CI cluster (MI355X, Pensando AINIC, rail-isolated RoCE fabric).

The AMD decode routing sidecar reaches its MoRI-IO peers by their `LeaderWorkerSet` pod DNS names, built from the pod's `disaggregatedset.x-k8s.io/revision` label, so no extra Services are needed. The peer list is fixed to this 2P2D shape (one two-pod group per role) and resolved once at startup: changing `replicas` or `size` means editing it and redeploying. This is a temporary workaround until multi-pod peer discovery is supported.

**Intel XPU** (DeepSeek-V2-Lite-Chat): prefill and decode each run TP2 with expert parallelism on 4 XPUs in total, with the `allgather_reducescatter` all-to-all backend, NIXL KV transfer (`kv_buffer_device=xpu`), and the `tcp,ze_copy` UCX transport for the validated non-RDMA configuration.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Have a cluster with RDMA-capable accelerator nodes. For the networking stack and how to verify it, see [RDMA and Networking Configuration](../../docs/infrastructure/rdma/README.md) and the [multi-node deployment guide](../../docs/infrastructure/multi-node.md). For GKE, see the [provider setup doc](../../docs/infrastructure/providers/gke/README.md) and the [GKE overlay cluster prerequisites](modelserver/gpu/vllm/gke/README.md#cluster-prerequisites).

> [!IMPORTANT]
> The NVIDIA GPU and AMD configurations use an RDMA all-to-all backend (DeepEP, MoRI) for inter-node EP and require all-to-all RDMA connectivity: every NIC on a host must be able to communicate with every NIC on all other hosts. Networks restricted to communicating only between matching NIC IDs (rail-only connectivity) will fail. The Intel XPU configuration uses XCCL and `allgather_reducescatter`; it does not use DeepEP, but still requires full-mesh pod network connectivity between decode and prefill workers.

- Deploy the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1` or newer. When installing with Helm, pass `--set enableDisaggregatedSet=true` to enable the `DisaggregatedSet` validating webhook and RBAC used by the NVIDIA GPU and AMD configurations.

- For Intel XPU, install the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) and verify that the `gpu.intel.com` DRA DeviceClass is available.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Install the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

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

**Set the guide-specific environment variables:**

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=wide-ep
export NAMESPACE=llm-d-wide-ep
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu
export MODEL_SERVER=vllm # options: vllm
export ACCELERATOR=${ACCELERATOR_TYPE} # keep equal to ACCELERATOR_TYPE
export INFRA_PROVIDER=base # options: base, gke, coreweave, amd-ci; valid values per accelerator: table above
export MODEL=deepseek-ai/DeepSeek-R1-0528 # set to the model your accelerator serves (table above)
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

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below). [`router/wide-ep.values.yaml`](router/wide-ep.values.yaml) configures P/D disaggregation and targets all eight DP rank ports of every pod; the per-accelerator file layered on top re-targets the router at the AMD or Intel XPU model servers:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
# Per-accelerator overrides: the AMD and Intel XPU ones re-target the router
# at their model servers; the NVIDIA GPU one is empty
export ROUTER_ACCELERATOR_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${ACCELERATOR}.values.yaml"
```
<!-- guide:deploy.router_values end -->

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -f ${ROUTER_ACCELERATOR_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your accelerator and infrastructure provider (see the table above for the valid `INFRA_PROVIDER` values):

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

### 3. (Optional) Enable Monitoring

With DP-aware scheduling, each DP rank is available at `podIP:port`, where the ports are named `rank0`-`rank7`. This guide ships a monitoring overlay that scrapes each rank's port:

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
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

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request end -->

## Mechanism Verification

These checks confirm that requests are spread across DP ranks and that every request is disaggregated: prefilled on a prefill rank, with its KV cache transferred to a decode rank.

**Send a batch of requests** with distinct prompts through the proxy:

<!-- guide:verify.tests.dp_requests start -->
```bash
# 32 requests, 8 at a time, each with a distinct ~1k-token prompt (no shared
# prefix, so prefix-cache affinity does not pin them to one DP rank): each is
# prefilled on a prefill DP rank, and its KV cache is transferred to a decode
# DP rank
kubectl run dp-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 60); do printf "Prefill ranks compute the KV cache and decode ranks pull it to generate tokens. "; done)
    for i in $(seq 1 32); do
      curl -sS -o /dev/null -w "%{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"Request ${i}. ${P} What is wide expert parallelism?\", \"max_tokens\": 32}" &
      [ $((i % 8)) -eq 0 ] && wait
    done | sort | uniq -c'
```
<!-- guide:verify.tests.dp_requests end -->

**Count the DP ranks that served them**, from each pod's vLLM metrics:

<!-- guide:verify.tests.rank_metrics start -->
```bash
# Requests and connector (KV transfer) prefix-cache hits per DP rank, read from
# every running model server pod through the Kubernetes API server proxy (no
# port-forward needed). NVIDIA GPU runs one API server per DP rank (prefill on
# ports 8000-8007, decode on 8200-8207 behind the routing sidecar); AMD and
# Intel XPU serve all of a pod's ranks on one port (prefill 8000, decode 8200)
for role in prefill decode; do
  first=8000; [ "${role}" = decode ] && first=8200
  ports=${first}; [ "${ACCELERATOR_TYPE}" = gpu ] && ports=$(seq ${first} $((first + 7)))
  pods=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=${role} \
    --field-selector=status.phase=Running -o jsonpath='{.items[*].metadata.name}')
  if [ -z "${pods}" ]; then echo "${role}: no running pods"; continue; fi
  for pod in ${pods}; do
    for port in ${ports}; do
      kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:${port}/proxy/metrics" \
        | awk -v k="${pod}:${port}" '/^vllm:(request_success|external_prefix_cache_hits)_total\{/ {
            e = match($0, /engine="[^"]*"/) ? substr($0, RSTART + 8, RLENGTH - 9) : "0"
            if ($0 ~ /^vllm:request_success/) r[k "/" e] += $NF; else h[k "/" e] += $NF
          } END { for (x in r) print x, r[x], h[x] + 0 }'
    done
  done | awk -v role="${role}" '$2 > 0 { n++; req += $2; hits += $3 }
    END { printf "%s: %d DP rank(s) served %d requests; connector prefix-cache hits: %d tokens\n", role, n, req, hits }'
done
```
<!-- guide:verify.tests.rank_metrics end -->

The script prints one line per role: the number of DP ranks that served requests, the requests they served, and the connector prefix-cache hits.

- **DP/EP**: on NVIDIA GPU and AMD, where each role spans 16 DP ranks, both roles report more than one DP rank. The router picks a rank per request, so the exact counts vary from run to run. On Intel XPU, each role is one TP2 engine with expert parallelism, so each reports one rank.
- **P/D**: the prefill pods served every request, and the decode pods report connector prefix-cache hits: tokens whose KV cache was transferred from a prefill rank instead of being recomputed. Without disaggregation, the prefill counters stay at zero.

If you enabled monitoring (`MONITORING=true`), the router also counts its P/D decisions:

<!-- guide:verify.tests.router_metrics start -->
```bash
# only when MONITORING=true:
# P/D decisions taken by the router, read through the API server service proxy
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_disagg_decision_total' || true
```
<!-- guide:verify.tests.router_metrics end -->

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
