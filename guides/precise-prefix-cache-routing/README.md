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
- The router needs exact token IDs to look a prompt up, so this guide also deploys a **render (tokenizer) Service** that the router calls before every routing decision.

The default deployment serves `Qwen/Qwen3-32B` on NVIDIA GPUs with two replicas at tensor parallelism 2. [How It Works](#how-it-works) shows how this guide wires the events, the render Service and the router together.

For why KV-cache events make a better source of truth than heuristics, and how the indexer ingests them and scores requests, see [Prefix-Cache Aware Routing](../../docs/architecture/kv-management/prefix-cache-aware-routing.md#2-precise-implementation) and the [KV-Cache Indexer](../../docs/architecture/kv-management/kv-indexer.md).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves exactly one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | 🟡 community | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` (SGLang uses the dedicated render pool) |
| AMD GPU | `amd` | `Qwen/Qwen3-32B` | ✅ validated | — | 2 replicas × TP=2 (4 GPUs) |
| Intel XPU | `xpu` | `Qwen/Qwen3-0.6B` | ✅ validated | — | 2 replicas × 1 GPU via DRA · fp16 |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | ✅ validated | — | GKE only · 2 replicas × 8 chips (`2x4`, TP=8) · vLLM 0.29 |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | 🟡 community | — | GKE only · 2 replicas × 4 chips (`2x2x1`, TP=8) · vLLM 0.29 |
| CPU | `cpu` | `meta-llama/Llama-3.2-3B-Instruct` | 🟡 community | — | 2 replicas |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

> [!NOTE]
> The router and the model servers must agree on two things:
>
> - **Block size.** vLLM `--block-size` and SGLang `--page-size` are `64`, matching the `precise-prefix-cache-producer`'s `tokenProcessorConfig.blockSizeTokens`; change them together.
> - **Model.** The `token-producer` `modelName` in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml) is `Qwen/Qwen3-32B`. On an accelerator that serves another model, set it to `MODEL` before deploying the router: with the default render Service the render call lands on the model servers themselves, so a mismatch is rejected outright rather than silently scoring against the wrong tokenizer.

For wide-EP LWS deployments (multi-port DP model servers), use the precise prefix-cache routing mode in the [GLM-5.2 guide](../models/glm-5-2/README.md#installation-instructions) instead of the manifests here.

> [!NOTE]
> The router runs as a **single replica**: the `token-load-scorer`'s in-flight token accounting is local to each router process, so two active-active replicas would each see only half the per-endpoint load and mis-gate the affinity filter. The precise KV index itself is HA-safe (each replica converges independently via pod discovery), so active-active HA (`--set router.epp.replicas=2`) can return once in-flight state is shared.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total). If your cluster has fewer resources, adjust `replicas` and `--tensor-parallel-size` in the [model server patch](./modelserver/gpu/vllm/base/patch-vllm.yaml) for your environment.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell. The router also reads it to reach gated tokenizers.

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
export GUIDE_NAME=precise-prefix-cache-routing
export NAMESPACE=llm-d-precise-prefix-cache-routing
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v6, tpu/v7, cpu
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
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

> [!NOTE]
> The `prefix-cache-affinity-filter` in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml) uses a `peakPrefillThroughput` measured on the reference setup (`Qwen3-32B` on H100 80&nbsp;GB, TP=2; SGLang measures the same value as vLLM). On a different model or accelerator, measure and set your value with the [calibration guide](../recipes/router/calibration/README.md).

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

Tokenization is served by a separate render Service (step 3), not by a router sidecar: the chart's `router.tokenizer` sidecar is off by default, and the `token-producer` plugin points at that Service.

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

### 3. Deploy the Render (Tokenizer) Service

The router's `token-producer` plugin tokenizes each prompt by calling vLLM's `/v1/*/render` endpoints, so it can look up the exact KV blocks the prompt maps to. This guide serves that endpoint from a Service rather than a per-router sidecar, so render capacity is decoupled from the router.

**Apply the render overlay for your model server.** Both overlays publish the same Service name, so the router configuration is the same either way:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

<!-- guide:deploy.render[0] start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```
<!-- guide:deploy.render[0] end -->

The `render/` overlay is a **Service with no pods of its own**: it selects the model server pods you just deployed and tokenizes on them. vLLM 0.30 requires `--enable-scale-out` for `vllm serve` to expose `/v1/*/render`; the NVIDIA GPU, AMD GPU, Intel XPU, and CPU overlays set it, and the TPU overlays use vLLM 0.29, which exposes the endpoint without the flag.
Render capacity scales with the fleet, which keeps render latency (part of TTFT, since every request is tokenized before it is routed) contained at higher QPS.
The trade-off: tokenization CPU competes with serving on the same pods (the GPU overlay requests 8 CPU / limits 16 per replica; raise that if render latency climbs under load).
Only `Ready` endpoints receive render calls, so apply this after the model servers, as ordered here.

</details>
<details>
<summary><b>SGLang</b></summary>

<!-- guide:deploy.render[1] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.render[1] end -->

SGLang does not implement vLLM's render endpoints, so the `render/standalone/` overlay runs a dedicated, GPU-less `vllm launch render` pool (3 replicas) instead.
It tokenizes with vLLM's tokenizer whichever engine serves inference, and also suits vLLM deployments that should not spend model server CPU on tokenization.
It is configured for `Qwen/Qwen3-32B`; for another model, change the model argument in [`render/standalone/deployment.yaml`](render/standalone/deployment.yaml) together with the router `token-producer` `modelName`. Scale it with `kubectl scale -n ${NAMESPACE} deploy/${GUIDE_NAME}-render --replicas=<N>`.
These render pods deliberately do **not** carry the `llm-d.ai/guide` label: the router would otherwise treat them as routable model servers.

</details>
<!-- tabs:end -->

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Check the Render Service

**Check that the render Service returns token IDs for your model** (`MODEL` must be the model your accelerator serves, see [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)). The response is a JSON list whose first entry carries `token_ids`:

<!-- guide:verify.tests.render start -->
```bash
# The render Service must return token IDs for MODEL before requests are routed
kubectl run render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="GUIDE_NAME=${GUIDE_NAME}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${GUIDE_NAME}-render:8000/v1/completions/render" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"render check\", \"max_tokens\": 1}"'
```
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

### 4. Verify precise prefix-cache aware routing

A served request only proves the stack is up. To confirm the routing mechanism this guide deploys is engaged, send a burst of requests that share a long prompt prefix, then check that the router kept them on the model server whose KV-cache events report that prefix.

**Send 10 requests that share a ~2k-token prefix:**

<!-- guide:verify.tests.shared_prefix start -->
```bash
# 10 requests that share a ~2k-token prefix and differ only in the last sentence
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
    | grep -E '^(vllm:prefix_cache_(hits|queries)_total|vllm:request_success_total|sglang:(cache_hit_rate|num_requests_total))' || true
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

One pod reports most of the requests in `sglang:num_requests_total`, and its `sglang:cache_hit_rate` is well above zero.

</details>
<!-- tabs:end -->

If the requests are spread evenly and hit rates stay near zero, the router is not receiving KV-cache events or cannot tokenize the prompt: re-run the render check above, confirm `--block-size` / `--page-size` match `blockSizeTokens`, and check the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`) for ZMQ subscription or `token-producer` errors. Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## How It Works

1. **Model server pods publish KV-cache events** — each pod (vLLM or SGLang) runs with `--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'` and `KV_EVENTS_ENDPOINT=tcp://*:5556`, binding its own ZMQ socket. On every KV block allocation/eviction, the server emits a ZMQ message. The GPU vLLM backend (v0.26.0+) additionally binds a ZMQ ROUTER socket on port 5559 and retains the last 10,000 batches in an in-memory replay buffer for index recovery.
2. **Router subscribes per pod** — pod discovery (`kvEventsConfig.discoverPods: true`) registers the `precise-prefix-cache-producer` as an extractor on the data-layer `endpoint-notification-source`, so each router replica installs a ZMQ subscriber per model server pod independently. All replicas converge to the same index. When a replay endpoint is available, each subscriber requests buffered events on first connect (or after a router restart) to rebuild its KV-block index without waiting for live traffic.
3. **Router tokenizes the prompt** — before it can look the prefix up in that index, the `token-producer` plugin POSTs the prompt to the render Service to get exact token IDs.
4. **Filter + score** — the `prefix-cache-affinity-filter` narrows candidates to the pods where the request's prefix blocks are resident (falling back to the least-loaded pods when the cache-warm set is saturated past `peakPrefillThroughput`), and the `token-load-scorer` picks the endpoint with the least in-flight token load among them.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.render start -->
<!-- variants:start -->
<details open data-when="MODEL_SERVER=vllm">
<summary><b>vLLM</b></summary>

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```

</details>
<details data-when="MODEL_SERVER=sglang">
<summary><b>SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:cleanup.render end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
