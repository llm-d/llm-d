# Precise Prefix Cache Routing

[![E2E (AMD ROCm)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-amd-ci-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-amd-ci-acc-rocm-vllm-x.yaml)
[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-intel-acc-xpu-vllm-x.yaml)

## Overview

This guide routes requests on precise per-pod KV-cache state rather than request-traffic heuristics. It builds on the [Optimized Baseline](../optimized-baseline/README.md) with the same `prefix-cache-affinity-filter` and `token-load-scorer`, but replaces the approximate prefix-cache estimate with the model servers' own record of what they cache:

- Each model server pod (vLLM or SGLang) publishes KV-cache events over ZMQ, and every router replica subscribes to every pod (pod discovery) to build an index of resident KV blocks.
- The [`precise-prefix-cache-producer`](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/dataproducer/preciseprefixcache) looks each request's prefix up in that index; the `prefix-cache-affinity-filter` keeps each prefix group on its cache-warm endpoints (gated by a calibrated `peakPrefillThroughput`), and the `token-load-scorer` picks the least token-loaded endpoint among them.
- The router needs exact token IDs to look a prompt up, so it **renders (tokenizes) every prompt** before routing it: with vLLM on the model servers themselves, picking one Ready endpoint of the pool per request; with SGLang, which has no render endpoints, on a dedicated vLLM render pool this guide deploys.

The default deployment serves `Qwen/Qwen3-32B` on NVIDIA GPUs with two replicas at tensor parallelism 2. [How It Works](#how-it-works) shows how this guide wires the events, rendering and the router together.

For why KV-cache events make a better source of truth than heuristics, and how the indexer ingests them and scores requests, see [Prefix-Cache Aware Routing](../../docs/architecture/kv-management/prefix-cache-aware-routing.md#2-precise-implementation) and the [KV-Cache Indexer](../../docs/architecture/kv-management/kv-indexer.md).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves exactly one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | 🟡 community | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` (SGLang uses the dedicated render pool) |
| AMD GPU | `amd` | `Qwen/Qwen3-32B` | ✅ validated | — | 2 replicas × TP=2 (4 GPUs) |
| Intel XPU | `xpu` | `Qwen/Qwen3-0.6B` | ✅ validated | 🟡 community | vLLM: Data Center GPU Max 1550+ · SGLang: Arc Pro B60 · 2 replicas × 1 XPU via DRA · fp16 (SGLang uses the dedicated render pool) |
| Google TPU v5p | `tpu/v5p` | `Qwen/Qwen3-32B` | ✅ validated | — | GKE only · 2 replicas × 4 chips (`2x2x1`, TP=4) · vLLM 0.29 · nightly runs on Spot capacity |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | 🟡 community | — | GKE only · 2 replicas × 8 chips (`2x4`, TP=8) · Torch TPU vLLM (`torch-tpu-vllm` image, 16k context) · nightly moved to `tpu/v5p` while v6e capacity is unavailable |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3.5-397B-A17B-FP8` | 🟡 community | — | GKE only · 2 replicas × 4 chips (`2x2x1`, DP=2 × TP=4, EP) · Torch TPU vLLM |
| CPU | `cpu` | `meta-llama/Llama-3.2-3B-Instruct` | 🟡 community | — | 2 replicas |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

> [!NOTE]
> The router and the model servers must agree on two things:
>
> - **Block size.** vLLM `--block-size` and SGLang `--page-size` are `64`, matching the `precise-prefix-cache-producer`'s `tokenProcessorConfig.blockSizeTokens`; change them together. **TPU v6e** is the exception: `--block-size=64` runs out of SMEM on Torch TPU, so the `tpu/v6` overlay sets `--block-size=256` and [`router/tpu-v6e.values.yaml`](router/tpu-v6e.values.yaml) sets `blockSizeTokens: 256` to match.
> - **Model.** The `token-producer` `modelName` in the [vLLM](router/precise-prefix-cache-routing.values.yaml) and [SGLang](router/precise-prefix-cache-routing.sglang.values.yaml) router values is `Qwen/Qwen3-32B`. On an accelerator that serves another model (e.g. Intel XPU's `Qwen/Qwen3-0.6B`), set it to `MODEL` in the values file you're deploying before installing the router: with vLLM the render call lands on the model servers themselves, so a mismatch is rejected outright rather than silently scoring against the wrong tokenizer.

For wide-EP LWS deployments (multi-port DP model servers), use the precise prefix-cache routing mode in the [GLM-5.2 guide](../models/glm-5-2/README.md#installation-instructions) instead of the manifests here.

**Google TPU on GKE.** Weights are downloaded once per node into `/var/cache/huggingface` (a `hostPath`), so size the boot disk for the checkpoint: ≥ 200 GB for Qwen3-32B on v6e (66 GB), ≥ 1 TB for Qwen3.5-397B-A17B-FP8 on TPU7x (406 GB: a 600 GB disk leaves only ~20 GiB of allocatable headroom once the image and compile cache are counted; the 100 GB node-auto-provisioning default is evicted mid-download).
Annotate the pods `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` under the `OPTIMIZE_UTILIZATION` autoscaler profile; the Deployment uses `strategy: Recreate` because TPU node pools are fixed-size.
The TPU7x overlay runs DP=2 × TP=4 so per-rank KV events are exercised with a small rank count; for the tuned Qwen3.5-397B layout (DP=8 × TP=1, EP, 262k context, host KV tier, MTP) see the [Qwen3.5-397B model guide](../models/qwen3-5-397b/README.md).

> [!NOTE]
> The router runs as a **single replica**: the `token-load-scorer`'s in-flight token accounting is local to each router process, so two active-active replicas would each see only half the per-endpoint load and mis-gate the affinity filter. The precise KV index is not HA-safe either: on a warm fleet, a replica that starts later than the others misses every prefix cached before its replay window and everything built on those, until they are evicted (see [Index recovery](#index-recovery)). Active-active HA (`--set router.epp.replicas=2`) therefore needs both shared in-flight state and a complete index at start, which today means the experimental [snapshot option](#optional-configuration).

### Supported APIs

Precise routing needs the router's token IDs to match the model server's exactly, so the router tokenizes each request with the render endpoint vLLM provides for that API:

| API | Router render call | vLLM 0.30 (NVIDIA, AMD, Intel, CPU; SGLang render pool) | vLLM 0.29 (TPU) |
| --- | --- | --- | --- |
| `/v1/completions` | `/v1/completions/render` | ✅ native | ✅ native |
| `/v1/chat/completions` | `/v1/chat/completions/render` | ✅ native | ✅ native |
| `/v1/messages` (Anthropic) | `/v1/messages/render` | ✅ native | ✅ native |
| `/v1/responses` | `/v1/responses/render` | 🟡 native, preview | ⚠️ legacy fallback |

The `token-producer` picks the render path with `vllm.messagesRenderMode` and `vllm.responsesRenderMode` in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml):

- `auto` (default): probe the render endpoint once and use the native endpoint when it exists, else fall back to `legacy`.
- `native`: always use the native endpoint, with no fallback; rendering fails if the render endpoint lacks it.
- `legacy`: translate the request into a chat-completions render call. This path is deprecated and does not guarantee token parity, so some precise matches are lost.

Responses support is a preview: vLLM renders it natively, but the router's token-parity coverage for Responses requests is still in progress. On the TPU overlays (vLLM 0.29, no `/v1/responses/render`), `auto` falls back to `legacy` and the router logs a deprecation message. vLLM 0.30 serves both APIs and their render endpoints, which the Messages and Responses requests in [Verification](#3-send-test-requests) exercise; with SGLang, whether those APIs are served depends on the SGLang server.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total). If your cluster has fewer resources, adjust `replicas` and `--tensor-parallel-size` in the model server patch for your environment (e.g., [patch for vLLM on GPU](./modelserver/gpu/vllm/base/patch-vllm.yaml)).

- For the Intel XPU overlay, install the accelerator's resource driver and verify its DeviceClass before deployment. These manifests use the `resource.k8s.io/v1` DRA APIs, which require **Kubernetes 1.34+**; on 1.32/1.33 the driver may install but the overlay will still fail to apply.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell. The router also reads it to reach gated tokenizers.

- (Optional) Set up the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

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
export GUIDE_NAME=precise-prefix-cache-routing
export NAMESPACE=llm-d-precise-prefix-cache-routing
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ENGINE_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v5p, tpu/v6, tpu/v7, cpu
export MODEL_SERVER=vllm # options: vllm, sglang
export INFRA_PROVIDER=base # options: base, gke
export MODEL=Qwen/Qwen3-32B # set to the model your accelerator serves (table above)
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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below):

<!-- guide:deploy.router_values start -->
<!-- variants:start -->
<details open data-when="MODEL_SERVER=vllm">
<summary><b>vLLM</b></summary>

```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```

</details>
<details data-when="MODEL_SERVER=sglang">
<summary><b>SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
export ENGINE_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.sglang.values.yaml"
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:deploy.router_values end -->

> [!NOTE]
> The `prefix-cache-affinity-filter` in the [vLLM](router/precise-prefix-cache-routing.values.yaml) and [SGLang](router/precise-prefix-cache-routing.sglang.values.yaml) router values uses a `peakPrefillThroughput` measured on the reference setup (`Qwen3-32B` on H100 80&nbsp;GB, TP=2). On a different model or accelerator, measure and set your value with the [calibration guide](../recipes/router/calibration/README.md).

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining `MONITORING_VALUES` (see [Collect Metrics](../../docs/operations/observability/metrics.md)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:deploy.monitoring_values.standard start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values.standard end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

<!-- guide:deploy.monitoring_values.gke start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true and INFRA_PROVIDER=gke:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values.gke end -->

</details>
<!-- tabs:end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  ${ENGINE_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

The router renders prompts over HTTP (see [step 3](#3-render-tokenizer-endpoints)), so the chart's `router.tokenizer` sidecar stays off.

### 2. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your backend (`INFRA_PROVIDER=gke` applies only to accelerators available on GKE: NVIDIA GPU, TPU, and CPU; use `base` elsewhere). Every overlay starts the model server with KV-cache events enabled (`--kv-events-config`) and exposes the per-pod ZMQ socket (port `5556`) the router subscribes to:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

The vLLM model server overlays drain in-flight requests during rollouts and scale-down: `--shutdown-timeout=45`, plus a 15s `preStop` sleep and a 75s termination grace period from the [`graceful-shutdown` component](../recipes/modelserver/components/graceful-shutdown/kustomization.yaml). To tune these, see [Graceful Shutdown & Request Draining](../../docs/operations/lifecycle/graceful-shutdown.md).

### 3. Render (Tokenizer) Endpoints

The router's `token-producer` plugin tokenizes each prompt by calling vLLM's `/v1/*/render` endpoints, so it can look up the exact KV blocks the prompt maps to.

<!-- tabs:start group=engine -->
<details open data-when="MODEL_SERVER=vllm">
<summary><b>vLLM</b></summary>

**Nothing to deploy.** The router renders on the model servers you just deployed: `vllm.endpointDiscovery` in the [router values](router/precise-prefix-cache-routing.values.yaml) sends each render call to one of the `Ready` endpoints of the InferencePool, round-robin, and retries once on another endpoint after a transport error, a timeout, or a 408, 429 or 5xx response.
vLLM 0.30 serves `/v1/*/render` only with `--enable-scale-out`; the NVIDIA GPU, AMD GPU, Intel XPU, and CPU overlays set it, and the TPU overlays use vLLM 0.29, which exposes the endpoint without the flag. Without it every render call returns 404 and the router routes without cache information, which the [render check](#2-check-rendering) catches.
Render capacity scales with the fleet, which keeps render latency (part of TTFT, since every request is tokenized before it is routed) contained at higher QPS.
The trade-off: tokenization CPU competes with serving on the same pods (the GPU overlay requests 8 CPU / limits 16 per replica; raise that if render latency climbs under load).

</details>
<details data-when="ACCELERATOR_TYPE=gpu;MODEL_SERVER=sglang">
<summary><b>NVIDIA GPU · SGLang</b></summary>

<!-- guide:deploy.render[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.render[0] end -->

SGLang does not implement vLLM's render endpoints, so the `render/standalone/` overlay runs a dedicated, GPU-less `vllm launch render` pool (3 replicas) instead.
It tokenizes with vLLM's tokenizer whichever engine serves inference. It also suits vLLM deployments that should not spend model server CPU on tokenization: apply it and replace `vllm.endpointDiscovery` in the vLLM router values with the SGLang values' `vllm.url`.
It is configured for `Qwen/Qwen3-32B`; for another model, change the model argument in [`render/standalone/deployment.yaml`](render/standalone/deployment.yaml) together with the router `token-producer` `modelName`. Scale it with `kubectl scale -n ${NAMESPACE} deploy/${GUIDE_NAME}-render --replicas=<N>`.
These render pods deliberately do **not** carry the `llm-d.ai/guide` label: the router would otherwise treat them as routable model servers.

</details>
<details data-when="ACCELERATOR_TYPE=xpu;MODEL_SERVER=sglang">
<summary><b>Intel XPU · SGLang</b></summary>

<!-- guide:deploy.render[1] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/xpu-sglang/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.render[1] end -->

Same standalone render pool as `render/standalone/`, but patched to tokenize `Qwen/Qwen3-0.6B` (the model served by the XPU overlay) instead of `Qwen/Qwen3-32B`.

</details>
<!-- tabs:end -->

**(Optional) Deploy the monitoring resources for model servers** (see [Collect Metrics](../../docs/operations/observability/metrics.md)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

No manual `PodMonitor` deployment is required when [GKE automatic application monitoring](../../docs/operations/observability/metrics.md#step-1-scrape-model-server-metrics) is enabled; GKE automatically provisions `PodMonitoring` resources for the model servers.

</details>
<!-- tabs:end -->

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Check Rendering

**Check that rendering returns token IDs for your model**: with vLLM on every model server pod, with SGLang on the render pool. The check prints `OK` per endpoint and exits non-zero on the first response without `token_ids`:

<!-- guide:verify.tests.render start -->
<!-- variants:start -->
<details open data-when="MODEL_SERVER=vllm">
<summary><b>vLLM</b></summary>

```bash
# Every model server pod must return token IDs for MODEL: the router
# renders on each of them in turn. Exits non-zero on the first pod that
# does not, e.g. a 404 when vLLM runs without --enable-scale-out.
export POD_IPS=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].status.podIP}')
kubectl run render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="POD_IPS=${POD_IPS}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'test -n "${POD_IPS}" || { echo "FAIL: no model server pods"; exit 1; }
    for ip in ${POD_IPS}; do
      R=$(curl -sS -X POST "http://${ip}:8000/v1/completions/render" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"render check\", \"max_tokens\": 1}")
      echo "${R}" | grep -q "\"token_ids\"" || { echo "FAIL: ${ip}: ${R}"; exit 1; }
      echo "OK: ${ip}"
    done'
```

</details>
<details data-when="MODEL_SERVER=sglang">
<summary><b>SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
# The render pool must return token IDs for MODEL before requests are routed
kubectl run render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="GUIDE_NAME=${GUIDE_NAME}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'R=$(curl -sS -X POST "http://${GUIDE_NAME}-render:8000/v1/completions/render" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"render check\", \"max_tokens\": 1}")
    echo "${R}" | grep -q "\"token_ids\"" || { echo "FAIL: ${R}"; exit 1; }
    echo "OK: ${GUIDE_NAME}-render"'
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:verify.tests.render end -->

### 3. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

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

**Send an Anthropic Messages request**, which the router tokenizes through `/v1/messages/render`:

<!-- guide:verify.tests.messages start -->
```bash
# Anthropic Messages API: the router tokenizes it through /v1/messages/render
kubectl run curl-messages --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/messages" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"max_tokens\": 16, \"messages\": [{\"role\": \"user\", \"content\": \"How are you today?\"}]}"'
```
<!-- guide:verify.tests.messages end -->

**Send a Responses request** (preview, see [Supported APIs](#supported-apis)), which the router tokenizes through `/v1/responses/render`:

<!-- guide:verify.tests.responses start -->
```bash
# OpenAI Responses API: the router tokenizes it through /v1/responses/render
kubectl run curl-responses --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/responses" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"max_output_tokens\": 16, \"input\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.responses end -->

### 4. Verify that the router renders

The [render check](#2-check-rendering) calls the render endpoints directly, so it passes even when the router's own render calls fail. Requests still return 200 in that case, routed without cache information. Confirm that the router renders a request itself, using its render metrics.

**Let the check pods read the router's metrics.** The router authenticates `/metrics` by default (a harmless no-op if `monitoring.values.yaml` turned that off), so grant the namespace's `default` service account read access:

<!-- guide:verify.tests.metrics_rbac start -->
```bash
# Let the default service account of the check pods read the router's
# /metrics, which the router authenticates by default. A harmless
# no-op when monitoring.values.yaml turned metrics auth off.
kubectl create clusterrole ${GUIDE_NAME}-metrics-reader \
  --verb=get --non-resource-url=/metrics \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding ${GUIDE_NAME}-metrics-reader \
  --clusterrole=${GUIDE_NAME}-metrics-reader \
  --serviceaccount=${NAMESPACE}:default \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:verify.tests.metrics_rbac end -->

**Send one request through the router and compare its render metrics before and after.** The check exits non-zero unless the request adds a successful render call (`llm_d_epp_token_producer_render_duration_seconds_count{result="success"}`) and no failed one (`llm_d_epp_token_producer_render_failures_total`):

<!-- guide:verify.tests.router_render start -->
```bash
# The router must render a request itself: one more successful render
# call and no new failed one across a request sent through it. The
# render check above calls the model servers directly, so it passes
# even when the router's own renders fail and it routes every request
# without cache information.
kubectl run router-render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  --env="GUIDE_NAME=${GUIDE_NAME}" \
  -- /bin/sh -c 'TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
    metrics() { curl -sSf -o /tmp/metrics -H "Authorization: Bearer ${TOKEN}" "http://${GUIDE_NAME}-epp:9090/metrics" || { echo "FAIL: cannot read the router metrics"; exit 1; }; }
    count() { awk -v re="$1" "\$0 ~ re {s += \$NF} END {print s + 0}" /tmp/metrics; }
    SUCCEEDED="^llm_d_epp_token_producer_render_duration_seconds_count.*result=\"success\""
    FAILED="^llm_d_epp_token_producer_render_failures_total"
    metrics; OK0=$(count "${SUCCEEDED}"); FAIL0=$(count "${FAILED}")
    curl -sS -o /dev/null -w "request: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"router render check\", \"max_tokens\": 1}"
    metrics; OK1=$(count "${SUCCEEDED}"); FAIL1=$(count "${FAILED}")
    echo "router renders: $((OK1 - OK0)) succeeded, $((FAIL1 - FAIL0)) failed"
    test $((OK1 - OK0)) -ge 1 && test "${FAIL1}" = "${FAIL0}" || { echo "FAIL: the router did not render the request"; exit 1; }'
```
<!-- guide:verify.tests.router_render end -->

### 5. Verify precise prefix-cache aware routing

A served request only proves the stack is up. To confirm the routing mechanism this guide deploys is engaged, send a burst of requests that share a long prompt prefix, then check that the router kept them on the model server whose KV-cache events report that prefix.

**Send 10 requests that share a ~2k-token prefix:**

<!-- guide:verify.tests.shared_prefix start -->
```bash
# 10 requests that share a ~2k-token prefix and differ only in the last
# sentence, 3 s apart: past the router's 2 s speculative-index TTL, so
# each one routes on the KV-cache events of the one before it
kubectl run prefix-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 100); do printf "The llm-d router keeps a conversation on the server whose KV-cache events report its prefix. "; done)
    for i in $(seq 1 10); do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question ${i}: what is llm-d?\", \"max_tokens\": 16}"
      sleep 3
    done'
```
<!-- guide:verify.tests.shared_prefix end -->

**Read the prefix-cache and request counters** of every model server pod:

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Prefix-cache and request counters of every model server pod, read
# through the Kubernetes API server proxy (no port-forward needed)
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^(vllm:prefix_cache_(hits|queries)_total|vllm:request_success_total|sglang:(cache_hit_rate|cached_tokens_total|num_requests_total))' || true
done
```
<!-- guide:verify.tests.pod_metrics end -->

What to expect:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

One pod reports most of the 10 requests in `vllm:request_success_total`, and its `vllm:prefix_cache_hits_total` is a large fraction of `vllm:prefix_cache_queries_total` (every request after the first reuses the cached prefix). The other pods show few or none of these requests.

</details>
<details>
<summary><b>SGLang</b></summary>

One pod reports most of the requests in `sglang:num_requests_total`. Check
that `sglang:cached_tokens_total{cache_source="device"}` increases after
requests with a shared prefix (`sglang:cache_hit_rate` is a rolling gauge that
drops back to `0` once the `prefix-test` burst finishes on `docker.io/lmsysorg/sglang:v0.5.20`,
and also reports `0` on the Intel XPU image `ghcr.io/llm-d/llm-d-xpu-sglang:v0.10.0`,
even when prefix cache hits occur).

</details>
<!-- tabs:end -->

If the requests are spread evenly and hit rates stay near zero, the router is not receiving KV-cache events or cannot tokenize the prompt: re-run the render checks above ([2](#2-check-rendering) and [4](#4-verify-that-the-router-renders)), confirm `--block-size` / `--page-size` match `blockSizeTokens`, and check the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`) for ZMQ subscription or `token-producer` errors. Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## How It Works

1. **Model server pods publish KV-cache events** — each pod (vLLM or SGLang) runs with `--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'` and `KV_EVENTS_ENDPOINT=tcp://*:5556`, binding its own ZMQ socket. On every KV block allocation/eviction, the server emits a ZMQ message. Every vLLM overlay additionally binds a ZMQ ROUTER socket on port 5559 (`KV_EVENTS_REPLAY_ENDPOINT`) and retains the last 10,000 event batches in an in-memory replay buffer for index recovery; SGLang has no replay buffer.
2. **Router subscribes per pod** — pod discovery (`kvEventsConfig.discoverPods: true`) registers the `precise-prefix-cache-producer` as an extractor on the data-layer `endpoint-notification-source`, so each router replica installs a ZMQ subscriber per model server pod independently. See [Index recovery](#index-recovery) for how a router that starts against warm model servers rebuilds the index.
3. **Router indexes only prefix-shaped cache groups** — vLLM tags each event with its cache group. The router indexes stores from full-attention groups (`full_attention`, `mla_attention`, `sink_full_attention`) whose hashes cover a contiguous token span, and skips groups such as sliding-window attention whose blocks do not form a prefix. Hybrid-attention models therefore index correctly instead of reporting false prefix residency; skipped events are counted in `llm_d_epp_kv_cache_events_stores_skipped_total{cache_kind,reason}`. The models in this guide use full attention only, so nothing is skipped.
4. **Router tokenizes the prompt** — before it can look the prefix up in that index, the `token-producer` plugin POSTs the request to the render endpoint that matches its API (see [Supported APIs](#supported-apis)) on one of the model servers (SGLang: the render pool) to get exact token IDs.
5. **Filter + score** — the `prefix-cache-affinity-filter` narrows candidates to the pods where the request's prefix blocks are resident (falling back to the least-loaded pods when the cache-warm set is saturated past `peakPrefillThroughput`), and the `token-load-scorer` picks the endpoint with the least in-flight token load among them.

### Index recovery

`podDiscoveryConfig.replaySocketPort: 5559` lets a subscriber ask a vLLM pod to resend its buffered events. A subscriber requests a replay when it first connects (for example after a router restart), when it joins a stream mid-way, and when it detects a gap in the event sequence. Replays are bounded so a router restart against a large, warm fleet stays cheap:

- At most 8 replays run at once per router process; the rest wait for capacity.
- Each replay has a 2-second per-attempt socket deadline inside a 2-minute end-to-end deadline. An interrupted replay resumes from the next sequence number instead of starting over.

After a router restart, expect the router logs to show `Requesting proactive replay on connect` and then `Replay complete` for each model server pod. This recovery needs a router with llm-d/llm-d-router#3338, #3401 and #3402 (merged 2026-10-13): the router chart this guide installs defaults to the `main` image, which has them, but v0.11.0 does not, and a v0.11.0 router restarted against warm pods stays blind to them (llm-d/llm-d-router#3124). The index then holds the blocks stored in each pod's last 10,000 event batches, with one important gap on a warm fleet:

- vLLM publishes `BlockStored` only when it computes and caches a new block; a prefix-cache hit publishes nothing. A block stored before the replay window therefore stays out of the index until the pod evicts it and stores it again.
- A store whose parent block is missing from the index is dropped (the router logs `Failed to get request key for parent block` at debug level; `stores_skipped_total` does not count it). So every block later built on top of such a prefix is dropped too: a shared system prompt cached before the window keeps every conversation that extends it out of the index.

On a fleet whose shared prefixes are older than the replay window, the index can therefore stay partial until those prefixes are evicted, not just until live traffic arrives. The model servers keep their caches throughout: recovery never requires restarting vLLM.

SGLang has no replay buffer, so its values leave `replaySocketPort` unset. With no buffer at all, a restarted router indexes each pod from live events only: everything the pod cached before the router started, and every block later built on it, stays out of the index until the pod evicts it.

## Optional Configuration

The settings below are commented out in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml); uncomment them under `kvEventsConfig` to use them.

- **Limit subscriptions to pods that publish events** with `podDiscoveryConfig.podLabelSelector`. The default (empty) selector subscribes to every model server pod in the pool. In a pool where some pods publish no KV events, such as decode-only pods in a prefill/decode deployment, set a selector (for example `llm-d.ai/role=prefill`) so the router does not dial those pods and retry indefinitely. Pods that stop matching lose their subscriber and their index entries. An invalid selector fails router startup.
- **Warm the index from a vLLM snapshot (experimental)** with `podDiscoveryConfig.snapshotSocketPort`. A subscriber that joins mid-stream, sees its engine restart, or misses events loads the pod's KV-event snapshot in place of a full replay, so a late-starting router covers each pod's whole cache, not just its replay window. The export briefly stalls the engine, for a time that grows with the cache size. This requires vLLM's KV-event snapshot support ([vllm-project/vllm#57789](https://github.com/vllm-project/vllm/pull/57789)), which is not in a vLLM release yet. Upgrade the router before setting `snapshot_endpoint` on vLLM: older routers cannot decode the event batches of an engine with snapshots enabled.

## Observability

### Metrics

With `MONITORING=true`, Prometheus scrapes the router's metrics. `indexerConfig.kvBlockIndexConfig.enableMetrics: true` (set in this guide) adds the KV-index metrics:

| Metric | What it tells you |
| --- | --- |
| `llm_d_epp_kv_cache_events_active_subscribers` | Subscribers the router manages, including those retrying a failed connection. Should equal the number of model server pods. |
| `llm_d_epp_kv_cache_events_messages_received_total{pod_identifier}` | Event ingestion per pod. A pod whose rate drops to zero while it serves traffic is not reaching the index. |
| `llm_d_epp_kv_cache_events_zmq_errors_total{pod_identifier,operation}`, `llm_d_epp_kv_cache_events_subscriber_reconnections_total{pod_identifier}` | Connection problems per pod. |
| `llm_d_epp_kv_cache_events_pool_queue_depth` vs `llm_d_epp_kv_cache_events_pool_capacity` | Event-processing backlog. A queue that keeps growing means the index lags the model servers; raise `kvEventsConfig.concurrency`. |
| `llm_d_epp_kv_cache_events_stores_skipped_total{cache_kind,reason}` | Events left out of the index (see [How It Works](#how-it-works)). |
| `llm_d_epp_kv_cache_index_lookup_hits_total` / `llm_d_epp_kv_cache_index_lookup_requests_total` | Average number of contiguous prefix blocks the best pod holds per lookup. Hits count only an unbroken chain from the first block, so they match what the model server can actually reuse. |
| `llm_d_epp_kv_cache_index_lookup_latency_seconds` | Index lookup latency, part of every routing decision. |
| `llm_d_epp_prefix_predicted_cached_tokens` / `llm_d_epp_prefix_prompt_tokens` | Per-request histograms; the ratio of their `_sum`s is the share of prompt tokens the router predicted the chosen endpoint had cached. Compare it with the model servers' prefix-cache hit rate (below). |

To tell **low prefix reuse** apart from an **incomplete index**, compare the same token share on both sides. On the router, divide `llm_d_epp_prefix_predicted_cached_tokens_sum` by `llm_d_epp_prefix_prompt_tokens_sum` (the prompt tokens the router predicted the chosen endpoint had cached); on the model servers, divide `vllm:prefix_cache_hits_total` by `vllm:prefix_cache_queries_total`. With a complete index the two track each other closely. If the model servers' share is clearly higher, the index is missing blocks: check the subscriber, error and queue metrics above, and see [Index recovery](#index-recovery). If both are low, the workload simply shares little prefix.

### Tracing

To trace the router, deploy an OpenTelemetry collector and layer [`tracing.values.yaml`](../recipes/router/features/tracing.values.yaml) onto the router install, as described in [Trace Requests](../../docs/operations/observability/tracing.md). Router tracing covers the `tokenize` call to the render endpoint and the KV-index lookups (`index_lookup` and related spans). Spans for KV-event processing (`events_receive`, `events_process`, `events_decode`) are separate and off by default, because events arrive at many times the request rate and would crowd request traces out of a shared sampler: set `kvEventsConfig.tracing: true` to add them while debugging.

## Running in Production

This section collects what an operator needs to run this guide beyond a demo. The settings it refers to are explained in the sections above.

- **Supported path.** vLLM 0.30 on NVIDIA GPU is the reference: it is validated nightly. Every vLLM overlay enables index recovery through replay; SGLang does not. AMD GPU and Intel XPU are validated nightly; TPU v5p is validated on vLLM 0.29, where Responses requests use the legacy render path. See [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers) and [Supported APIs](#supported-apis).
- **Router replicas.** Run one router replica (see the note under [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)). A router restart therefore routes from a partial index until replay completes and, on a warm fleet, until prefixes stored before the replay window are evicted and stored again (see [Index recovery](#index-recovery)).
- **Metrics to watch.** Alert when a pod's `llm_d_epp_kv_cache_events_messages_received_total` rate is zero while it serves traffic (this catches a pod that is up but publishes on the wrong port or topic, which `active_subscribers` does not, since it counts subscribers that are still retrying), when `llm_d_epp_kv_cache_events_zmq_errors_total` rises for a pod, and when `llm_d_epp_kv_cache_events_pool_queue_depth` keeps growing. `active_subscribers` is not a health check: the router creates exactly one subscriber per pod `podLabelSelector` matches, so it equals that count by construction and a wrong selector still passes. Instead, compare it with the number of pods that run with `--kv-events-config`. Track the router's predicted cached-token share against the model servers' prefix-cache hit rate (see [Metrics](#metrics)).
- **Render.** Every request is rendered before it is routed, so a failed render routes it without cache information and a slow one adds to its TTFT. One attempt could otherwise use the request's whole render budget (`vllm.timeout`, 5 s by default; `vllm.mmTimeout`, 30 s, for chat, Messages and Responses) and leave no time for the retry on another endpoint, so this guide caps each attempt with `vllm.endpointDiscovery.attemptTimeout: 2s`; raise it for multimodal prompts. Watch `llm_d_epp_token_producer_render_failures_total` and `llm_d_epp_token_producer_render_duration_seconds` (see the [router metric reference](../../docs/operations/observability/router-metrics.md)).
- **Recovery.** If a pod's events stop reaching the router (its `messages_received_total` rate is zero, or its ZMQ errors keep rising), check that the pod exposes port `5556` (and `5559` on vLLM) and that its `--kv-events-config` topic matches `kvEventsConfig.topicFilter`. Restarting the router (`kubectl rollout restart -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`) re-subscribes to every pod without restarting the model servers, but it drops the index for every pod and rebuilds only each pod's replay window (see [Index recovery](#index-recovery)). On a warm fleet that can cost more routing quality than one broken stream, so fix the pod's configuration first and restart the router only when the index is broadly wrong. The experimental [snapshot option](#optional-configuration) is meant to make such a restart keep full coverage. If render calls fail, re-run the [render check](#2-check-rendering).

<!-- TODO(#2745): define the fail-fast rollout gates (which signals block a rollout, and their thresholds) with the router owners. Per-endpoint KV-event health (llm-d/llm-d-router#1004) is the likely gating signal. -->

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.render start -->
<!-- variants:start -->
<details data-when="ACCELERATOR_TYPE=gpu;MODEL_SERVER=sglang">
<summary><b>NVIDIA GPU · SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```
<!-- llm-d-cicd:skip end -->

</details>
<details data-when="ACCELERATOR_TYPE=xpu;MODEL_SERVER=sglang">
<summary><b>Intel XPU · SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/xpu-sglang/
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:cleanup.render end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true

kubectl delete clusterrolebinding ${GUIDE_NAME}-metrics-reader --ignore-not-found=true
kubectl delete clusterrole ${GUIDE_NAME}-metrics-reader --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
