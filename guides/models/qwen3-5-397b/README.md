# Qwen3.5-397B on TPU 7x

This recipe is optimized for agentic code generation; see the
[agentic workloads section of the Models overview](../../../docs/well-lit-paths/models/README.md#agentic-workloads)
for the workload framing, and the [GLM-5.2](../glm-5-2/README.md) and
[NVIDIA-Nemotron-3-Ultra](../nemotron-3-ultra/README.md) guides for the other models benchmarked
against the same workload.

## Overview

This guide deploys the optimal llm-d configuration for agentic code-generation workload. The configuration includes multiple llm-d optimizations in terms of routing and KV cache management:

- **Prefix-aware routing** to optimize prefix cache reuse
- **Tiered prefix cache (KV cache offloading)** to host DRAM (`TPURaidenOffloadingConnector` via TPU Sync on `unified` and on the `disaggregated` prefill tier) to handle multi-turn conversations with long contexts. On `unified` the host tier is per DP engine: a prefix is reusable from the host tier only on the engine that produced it, and vLLM's data-parallel balancer, not the llm-d router, decides which engine inside a pod serves a request
- **MTP speculative decoding** (3 draft tokens on `unified` and `decode`, 1 draft token on `prefill` to warm draft-layer KV) to reduce inter-token latency during code generation
- **Load balancing** to increase cluster-wide accelerator utilization and prevent hot-spotting from bursty request patterns

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [Qwen/Qwen3.5-397B-A17B-FP8](https://huggingface.co/Qwen/Qwen3.5-397B-A17B-FP8) |
| Replicas | 2 (`unified`) · 1 prefill + 1 decode (`disaggregated`) |
| Accelerator | Google TPU 7x (tpu7x) |
| Topology | 2x2x1 |
| Parallelism | `unified`: DP=8 × TP=1, EP · `disaggregated`: prefill PCP=8, decode DP=8, EP |
| Context length | 262,144 tokens (fp8 KV cache, 4352-token blocks), matching the benchmark workload |
| KV cache offload | `TPURaidenOffloadingConnector`, `cpu_bytes_to_use` is **per engine**: `unified` runs 8 DP engines per pod with 32 GiB each (8 independent tiers, 256 GiB pinned per pod); the `disaggregated` prefill is one PCP=8 engine with one 64 GiB tier |
| MTP speculative decoding | On — 3 draft tokens (`unified` and `decode`), 1 draft token (`prefill`) |
| Model server | vLLM on Torch TPU (`torch-tpu-vllm` image) |

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| Google TPU v7x | `tpu` | `Qwen/Qwen3.5-397B-A17B-FP8` | 🟡 community | GKE only · Torch TPU vLLM · `unified`: 2 replicas × 4 chips (`tpu7x` `2x2x1`, DP=8 × TP=1, EP, 262k context, host KV offload) · `TOPOLOGY=disaggregated`: a DisaggregatedSet with 1 prefill (PCP=8) + 1 decode (DP=8), TPU Sync P/D transfer and prefill host KV offload; scale to xPyD by raising the role replicas |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

Set `TOPOLOGY` to pick the serving topology: `unified` (default) runs one pool of 2 replicas with host KV offloading;
`disaggregated` runs the Torch TPU (`tpu-sync`) Prefill/Decode configuration as a `DisaggregatedSet`
(1 prefill + 1 decode, prefill host KV offload; the same overlay as the TPU7x row of the
[P/D disaggregation guide](../../pd-disaggregation/README.md)). Raise a role's `replicas` for xPyD.

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- A GKE cluster with a TPU v7x (`tpu7x`, `2x2x1`) node pool: 2 nodes for `unified`, 2 nodes
  (one prefill, one decode) for `disaggregated`, 4 chips each. Weights are downloaded once per node
  into `/var/cache/huggingface` (a `hostPath`), so nodes need a boot disk of at least 1 TB (a 600 GB disk holds the 406 GB checkpoint
  with only ~20 GiB of allocatable headroom) to keep it above the kubelet eviction threshold.

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

**Set the guide-specific environment variables:**

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=qwen3-5-397b
export NAMESPACE=llm-d-qwen3-5-397b
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=tpu # options: tpu
export MODEL_SERVER=vllm # options: vllm
export TOPOLOGY=unified # options: unified, disaggregated
export MODEL=Qwen/Qwen3.5-397B-A17B-FP8 # the model both topologies serve
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

**(`TOPOLOGY=disaggregated` only) Install the LeaderWorkerSet controller with the `DisaggregatedSet` API** (skip if your cluster already runs LWS `v0.11.1` or newer with `enableDisaggregatedSet=true`):

<!-- guide:prerequisites.lws[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when TOPOLOGY=disaggregated:
# helm upgrade never updates CRDs, so apply the chart's CRDs first (see LWS
# "Upgrade by Helm"); this upgrades them on clusters that run an older LWS.
helm pull oci://registry.k8s.io/lws/charts/lws --version=${LWS_VERSION} --untar
kubectl apply --server-side --force-conflicts -f lws/crds
helm upgrade --install lws oci://registry.k8s.io/lws/charts/lws \
  --version=${LWS_VERSION} \
  --namespace lws-system --create-namespace \
  --set enableDisaggregatedSet=true \
  --wait --timeout 300s
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.lws[0] end -->

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

**Prepare the paths to the `helm` values files** for the `llm-d` router. The P/D disaggregated
topology uses its own router values
([`router/qwen3-5-397b-disagg.values.yaml`](router/qwen3-5-397b-disagg.values.yaml)):

<!-- tabs:start group=topology -->
<details open>
<summary><b>Unified (Default)</b></summary>

<!-- guide:deploy.router_values.unified start -->
```bash
# only when TOPOLOGY=unified:
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values.unified end -->

</details>
<details>
<summary><b>Disaggregated (Experimental)</b></summary>

<!-- guide:deploy.router_values.disaggregated start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when TOPOLOGY=disaggregated:
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}-disagg.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.router_values.disaggregated end -->

</details>
<!-- tabs:end -->

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

> [!NOTE]
> **`peakPrefillThroughput` is hardware/model-specific.** The router values set
> `peakPrefillThroughput: 16444` (carried over from `Qwen3-Coder-480B-FP8` on TPU 7x as a proxy until `Qwen3.5-397B-A17B-FP8` is calibrated). To re-calibrate for your deployment or hardware, measure your own value and update
> `router/qwen3-5-397b.values.yaml` (and `router/qwen3-5-397b-disagg.values.yaml`). See the shared
> [router calibration tool](../../recipes/router/calibration/README.md).

**Deploy the router** in [Standalone Mode](../../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router. Keep the release name `${GUIDE_NAME}`: the verification steps look up the `${GUIDE_NAME}-epp` Service by that name. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

**Apply the Kustomize overlay** for your topology (the unified overlay enables CPU KV offloading):

<!-- tabs:start group=topology -->
<details open>
<summary><b>Unified (Default)</b></summary>

<!-- guide:deploy.modelserver.unified start -->
```bash
# only when TOPOLOGY=unified:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/
```
<!-- guide:deploy.modelserver.unified end -->

</details>
<details>
<summary><b>Disaggregated (Experimental)</b></summary>

<!-- guide:deploy.modelserver.disaggregated start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when TOPOLOGY=disaggregated:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}-disaggregated/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.disaggregated end -->

</details>
<!-- tabs:end -->

Wait for the model server to become ready:

```bash
# TOPOLOGY=unified
kubectl rollout status deployment/agentic-serving-tpu-vllm-decode -n ${NAMESPACE}
# TOPOLOGY=disaggregated
kubectl wait --for=condition=Available disaggregatedset/pd-disagg-tpu -n ${NAMESPACE} --timeout=1800s
```

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
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

## Benchmarking

This guide comes with an `inference-perf` benchmark preset (defined in [inference-perf.yaml](inference-perf.yaml)) designed for agentic code-generation workloads with multi-turn interactions and tool usage. The configuration parameters include:

| Workload Characteristic | Metric / Distribution Type | Min | Max | Mean / Constant | Std Dev | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shared System Prompt** | Constant | - | - | 3,000 tokens | - | Common base instructions, libraries, and API schemas shared across all agent instances. Highly cacheable. |
| **Dynamic System Prompt** | Lognormal | 10,000 | 990,000 | 160,000 tokens | 233,600 | Repository context, file indexes, and user-specific code context. Extremely large and variable context. |
| **Turns per Conversation** | Lognormal | 1 | 3,000 | 540 turns | 48,600 | The depth of the agentic reasoning/conversational loop. Multi-turn interactions require sustaining long-lived sessions. |
| **Input Tokens per Turn** | Lognormal | 100 | 10,000 | 1,500 tokens | 1,200 | Ongoing prompt extensions (e.g., test logs, user follow-ups, modified code blocks) during conversation. |
| **Output Tokens per Turn** | Lognormal | 50 | 10,000 | 425 tokens | 825 | Model generations per turn, which are generally smaller than inputs but can spike when generating large files. |
| **Tool Call Latency** | Lognormal | 1s | 100s | 15 seconds | 55 | Time spent executing tools (compilation, unit tests, web search). Causes idle/delayed turns on the client side. |

### 1. Run the Benchmark

[`inference-perf.yaml`](inference-perf.yaml) runs an
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) job that replays the workload
above against the `${GUIDE_NAME}-epp` Service. Set the per-run parameters, render it, and apply it:

```bash
# Benchmark parameters. CONCURRENCY_LEVEL is the number of concurrent coding sessions
# to drive; NUM_REQUESTS is fixed at 20 per session; SEED is varied per concurrency level
# so prompts don't overlap across runs (matches the published results below).
export CONCURRENCY_LEVEL=40
export NUM_REQUESTS=$((20 * CONCURRENCY_LEVEL))
export SEED=$((7 + CONCURRENCY_LEVEL))

kubectl delete job inference-perf -n ${NAMESPACE} --ignore-not-found
envsubst < ${REPO_ROOT}/guides/models/${GUIDE_NAME}/inference-perf.yaml | kubectl apply -n ${NAMESPACE} -f -
```

### 2. Collect the Results

Follow the job's logs until it completes:

```bash
kubectl logs -n ${NAMESPACE} job/inference-perf -f
```

> [!TIP]
> **Tuning Prefill-to-Decode Ratios:**
> The `vllm-disaggregated` overlay (`TOPOLOGY=disaggregated`) deploys 1 prefill + 1 decode. Each pod derives its TPU Sync engine ID from its own name, so other ratios are a replica change on the `DisaggregatedSet` roles (role 0 = prefill, role 1 = decode):
>
> ```bash
> kubectl patch disaggregatedset/pd-disagg-tpu -n ${NAMESPACE} --type=json \
>   -p '[{"op": "replace", "path": "/spec/roles/0/spec/replicas", "value": 2},
>        {"op": "replace", "path": "/spec/roles/1/spec/replicas", "value": 6}]'
> ```

## Benchmark Results

Results for `Qwen/Qwen3.5-397B-A17B-FP8` on Torch TPU are being collected with the workload above
(`inference-perf` code-generation profile and the AgentX harness) for both topologies and will be
published here with the model, slice, image digest and benchmark parameters of each run.

> [!NOTE]
> The previous revision of this guide served `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` on the JAX
> `tpu_inference` backend; its results (June–July 2026) are in the
> [git history](https://github.com/llm-d/llm-d/commits/main/guides/models/qwen3-coder-480b) and do not
> apply to this configuration.

## Cleanup

To remove the deployed components:

<!-- tabs:start group=topology -->
<details open>
<summary><b>Unified (Default)</b></summary>

<!-- guide:cleanup.modelserver.unified start -->
```bash
# only when TOPOLOGY=unified:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/
```
<!-- guide:cleanup.modelserver.unified end -->

</details>
<details>
<summary><b>Disaggregated (Experimental)</b></summary>

<!-- guide:cleanup.modelserver.disaggregated start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when TOPOLOGY=disaggregated:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}-disaggregated/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.modelserver.disaggregated end -->

</details>
<!-- tabs:end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
