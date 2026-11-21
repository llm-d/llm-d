# P/D Disaggregation with the Coordinator

## Overview

> [!WARNING]
> This is an **experimental architecture**. It is less mature and less tested than
> [P/D disaggregation with the routing sidecar](../sidecar/README.md), and may change in
> upcoming releases.

This guide deploys the same prefill and decode pools as the [sidecar guide](../sidecar/README.md), but drives the two phases differently. A standalone llm-d **Coordinator** receives each request and runs a `prefill → decode` pipeline over it. For each phase it calls the llm-d Router through a Kubernetes Gateway. The decode pod is therefore picked only after prefill has finished, based on the pool's state at that time, and the decode pods run no routing sidecar.

One Endpoint Picker (EPP) and one `InferencePool` serve both phases. The Coordinator names the phase of each call in the `x-llm-d-epp-profile` header, and the EPP's `header-profile-handler` runs exactly that scheduling profile. The two profiles are the same as in the sidecar guide:

- **Prefill**: the `prefix-cache-affinity-filter` keeps prefix groups on cache-warm prefill pods, and the `token-load-scorer` picks the prefill pod with the least queued prompt work.
- **Decode**: the `active-request-scorer` picks the decode pod with the fewest in-flight requests.

Every request is disaggregated, as with the sidecar guide's `always-disagg-pd-decider`. The default deployment serves `openai/gpt-oss-120b` on NVIDIA GPUs with **1 prefill pod (TP=1) and 1 decode pod (TP=4)**, 5 GPUs in total, as one [`DisaggregatedSet`](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) (`pd-disagg-vllm`).

For the Coordinator's design, its benchmarks against the sidecar, and when the later decode pick pays off, see [Coordinator Disaggregation](../../coord-disaggregation/README.md). [The P/D overview](../README.md) compares the two guides.

This guide covers:

- **vLLM on NVIDIA GPU and on Google TPU7x dynamic sub-slices.** SGLang is not supported. The TPU7x variant has not been run end to end with the Coordinator yet (see its [model server tab](#3-deploy-the-model-server)).
- **Gateway Mode only.** The Coordinator sends every phase call back through a Kubernetes Gateway, so the router's Standalone Mode is not supported.
- **One EPP for both phases.** The per-role EPP topology of the [Coordinator Disaggregation](../../coord-disaggregation/README.md) guide is not covered.

## Supported Accelerators and Model Servers

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `openai/gpt-oss-120b` | 🟡 community | H200 · 1P (TP=1) + 1D (TP=4), 5 GPUs · NIXL · Coordinator behind a Kubernetes Gateway |
| Google TPU v7 (dynamic slicing) | `tpu/v7-dynamic-slice` | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | 🟡 community | TPU7x sub-slices via dynamic slicing + Kueue · `DisaggregatedSet` with 1P + 1D, one `2x2x1` sub-slice each · TPUConnectorHMA · not yet run end to end |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

KV cache moves from prefill to decode with vLLM's `NixlConnector` on NVIDIA GPU and `TPUConnectorHMA` on TPU7x. NIXL works over TCP, but RDMA networking is **highly recommended**; the NVIDIA GPU `gke` overlay uses GPU DRA and DRANet RoCE.

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md), plus `kustomize` and `envsubst`.

- Ensure your cluster has enough accelerators: 1 prefill pod with tensor parallelism 1 and 1 decode pod with tensor parallelism 4, 5 GPUs in total. The pods exchange KV cache over the network, so place them on nodes connected by a high-bandwidth fabric.

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Set up the [monitoring stack](../../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

- The [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1` or newer with the `DisaggregatedSet` API enabled. The [environment step](#configure-the-environment) below installs it; skip that step if your cluster already runs it.

- A Kubernetes Gateway named `llm-d-inference-gateway` in the guide namespace, deployed with one of [the gateway guides](../../../docs/infrastructure/gateway) after you create the namespace below.

- For `INFRA_PROVIDER=gke` on NVIDIA GPU: GKE A3 Ultra / A4 nodes with GPU DRA and managed DRANet for RoCE. See [Platform prerequisites](../README.md#platform-prerequisites) and [GKE: GPU DRA and DRANET](../../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke).

- For `ACCELERATOR_TYPE=tpu/v7-dynamic-slice`: pre-provisioned TPU7x `4x4x4` sub-blocks, with the cluster preparation and Kueue resources from [GKE dynamic slicing](../../../docs/infrastructure/providers/gke/README.md#tpu-dynamic-slicing-on-gke), which also installs the LeaderWorkerSet controller.

### Get the guide

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

`INFRA_PROVIDER` selects the model server overlay: on NVIDIA GPU, `gke` for GKE with GPU DRA and DRANet RoCE and `base` for a generic Kubernetes cluster; `tpu/v7-dynamic-slice` takes `gke` only. Set `MODEL` to the model your accelerator serves ([Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)). `GW_PROVIDER_NAME` is the provider of your Gateway.

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=pd-disaggregation-coordinator
export NAMESPACE=llm-d-pd-disaggregation-coordinator
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, tpu/v7-dynamic-slice
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=gke # options: base, gke; gke: GPU DRA and DRANet RoCE on NVIDIA GPU, the GKE TPU7x sub-slices on TPU (see Prerequisites); TPU takes gke only
export GW_PROVIDER_NAME=gke # options: gke, istio, agentgateway
export MODEL=openai/gpt-oss-120b # tpu/v7-dynamic-slice serves Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

**Install the LeaderWorkerSet controller with the `DisaggregatedSet` API** (skip if your cluster already runs LWS `v0.11.1` or newer with `enableDisaggregatedSet=true`; for TPU7x dynamic sub-slices, the GKE dynamic slicing cluster preparation installs it instead):

<!-- guide:prerequisites.lws start -->
<!-- variants:start -->
<details open data-when="ACCELERATOR_TYPE=gpu">
<summary><b>NVIDIA GPU</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
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

</details>
<!-- variants:end -->
<!-- guide:prerequisites.lws end -->

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

**Deploy the Gateway** named `llm-d-inference-gateway` in `${NAMESPACE}` by following one of [the gateway guides](../../../docs/infrastructure/gateway). On GKE, wait until it is `PROGRAMMED` (see [the GKE gateway guide](../../../docs/infrastructure/gateway/gke.md#step-3-verify-the-gateway)): the Coordinator step below reads its address.

## Installation Instructions

### 1. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/pd-disaggregation/coord/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

[`router/pd-disaggregation-coordinator.values.yaml`](router/pd-disaggregation-coordinator.values.yaml) configures the EPP with the `header-profile-handler` and the `prefill` and `decode` profiles. `header-profile-handler` is an Alpha plugin, so the values enable `allow-experimental-plugins`. They also disable the chart's own HTTPRoute, because the Coordinator step ships the routes.

> [!NOTE]
> The `prefix-cache-affinity-filter` uses a `peakPrefillThroughput` of `33821`, calibrated for gpt-oss-120b on TP=1 H200 prefill workers through the sidecar guide's P/D path. On a different model or accelerator, measure and set your value with the [calibration guide](../../recipes/router/calibration/README.md) before performance work.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining `MONITORING_VALUES` (see [Collect Metrics](../../../docs/operations/observability/metrics.md)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:deploy.monitoring_values.standard start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values.standard end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

<!-- guide:deploy.monitoring_values.gke start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true and GW_PROVIDER_NAME=gke:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values.gke end -->

</details>
<!-- tabs:end -->

**Deploy the router behind the Gateway.** The release name `${GUIDE_NAME}` is mandatory: it is also the name of the `InferencePool` that the Coordinator's HTTPRoute targets.

<!-- guide:deploy.gateway start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${GW_PROVIDER_NAME} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.gateway end -->

### 2. Deploy the Coordinator

**Point the Coordinator at the Gateway.** The Coordinator sends every phase call through the Gateway, so `GATEWAY_ADDRESS` must be reachable from inside the cluster:

<!-- guide:deploy.coordinator[0] start -->
```bash
case "${GW_PROVIDER_NAME}" in
  # GKE Gateway is a Google Cloud load balancer with no in-cluster Service:
  # use the address published on the Gateway's status.
  gke)
    export COORDINATOR_OVERLAY=gke
    export GATEWAY_ADDRESS="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}'):80"
    ;;
  # agentgateway names its Service after the Gateway, with no provider suffix.
  agentgateway)
    export COORDINATOR_OVERLAY=base
    export GATEWAY_ADDRESS="http://llm-d-inference-gateway.${NAMESPACE}.svc:80"
    ;;
  *)
    export COORDINATOR_OVERLAY=base
    export GATEWAY_ADDRESS="http://llm-d-inference-gateway-${GW_PROVIDER_NAME}.${NAMESPACE}.svc:80"
    ;;
esac
echo "GATEWAY_ADDRESS=${GATEWAY_ADDRESS}" # must be non-empty
```
<!-- guide:deploy.coordinator[0] end -->

**Deploy the Coordinator and its HTTPRoutes** from [`coordinator/`](coordinator/), set up as in the [Coordinator Disaggregation](../../coord-disaggregation/README.md) guide. Its pipeline is `prefill → decode` ([`coordinator/base/configmap.yaml`](coordinator/base/configmap.yaml)). As in that guide, two HTTPRoutes on the Gateway ([`coordinator/base/httproute.yaml`](coordinator/base/httproute.yaml)) split the traffic:

- `coordinator`: client calls to the inference paths (`/v1/completions`, `/v1/chat/completions`, `/v1/responses`, `/v1/messages`, `/inference/v1/generate`) go to the Coordinator.
- `pd-disaggregation-coordinator`: the Coordinator's own phase calls carry `x-llm-d-epp-profile` and go to the `InferencePool`, as does every other path (for example `/v1/models`).

<!-- guide:deploy.coordinator[1] start -->
```bash
kustomize build ${REPO_ROOT}/guides/pd-disaggregation/coord/coordinator/${COORDINATOR_OVERLAY}/ | envsubst '${GATEWAY_ADDRESS}' | kubectl apply -n ${NAMESPACE} -f -
```
<!-- guide:deploy.coordinator[1] end -->

The `gke` overlay adds a `HealthCheckPolicy` and a 300s `GCPBackendPolicy` for the Coordinator Service, and drops the HTTPRoutes' `timeouts` field, which GKE Gateway does not implement. The router chart already attaches the equivalent policies to the `InferencePool`.

> [!WARNING]
> `x-llm-d-epp-profile` is a plain client-controllable header, not a trust boundary: a client that sets it bypasses the Coordinator and reaches the router directly. Do not expose this Gateway to untrusted clients without network-level isolation; see the [Coordinator Disaggregation](../../coord-disaggregation/README.md#1-deploy-the-llm-d-router) guide.

### 3. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your accelerator. Each overlay deploys a prefill role (pods labeled `llm-d.ai/role=prefill`) and a decode role (`llm-d.ai/role=decode`) as one `DisaggregatedSet`, built on the same model server manifests as the sidecar guide ([`../modelserver/`](../modelserver/)), but decode runs no routing sidecar and serves port 8000 directly.

<!-- tabs:start group=modelserver -->
<details open>
<summary><b>NVIDIA GPU</b></summary>

<!-- guide:deploy.modelserver.standard[0] start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/pd-disaggregation/coord/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.standard[0] end -->

[`modelserver/gpu/vllm/base`](modelserver/gpu/vllm/base/) applies [`../modelserver/gpu/vllm/common`](../modelserver/gpu/vllm/common/), and the `gke` overlay adds one GPU and RoCE NIC claim per rank and a node-local HuggingFace cache. The overlays enable tool calling and reasoning parsing with `--enable-auto-tool-choice --tool-call-parser=openai --reasoning-parser=openai_gptoss`.

</details>
<details data-when="ACCELERATOR_TYPE=tpu/v7-dynamic-slice">
<summary><b>Google TPU v7 (dynamic slicing)</b></summary>

<!-- guide:deploy.modelserver.dynamic_slice[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
# One DisaggregatedSet; each role's LeaderWorkerSet group gets a 2x2x1 sub-slice, admitted by Kueue
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/docs/infrastructure/providers/gke/dynamic-slicing/kueue-localqueue.yaml
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/pd-disaggregation/coord/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.dynamic_slice[0] end -->

> [!WARNING]
> Not yet run end to end with the Coordinator: a run needs a full TPU7x `4x4x4` sub-block (64 chips), which llm-d CI does not have. The routing sidecar drives this overlay's `TPUConnectorHMA` with its `nixlv2` protocol, the same prefill-then-decode `kv_transfer_params` exchange as the Coordinator's `kv-nixl` connector.

`ACCELERATOR_TYPE=tpu/v7-dynamic-slice` deploys `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` (`TP=8` per `2x2x1` sub-slice) as one `DisaggregatedSet` (`pd-disagg-tpu-vllm`) with a `prefill` and a `decode` role of one replica each, from [`../modelserver/tpu/v7-dynamic-slice/vllm/common`](../modelserver/tpu/v7-dynamic-slice/vllm/common/). Kueue Topology-Aware Scheduling places each role's LeaderWorkerSet group on a sub-slice that [GKE dynamic slicing](../../../docs/infrastructure/providers/gke/README.md#tpu-dynamic-slicing-on-gke) forms on demand. Pods are admitted once their `Slice` resources are `ACTIVE` (`kubectl get slices -n ${NAMESPACE}`). The overlay sets `--enable-auto-tool-choice --tool-call-parser=qwen3_xml` and no reasoning parser.

The router values keep the gpt-oss-120b H200 `peakPrefillThroughput`; calibrate it for this model with the [calibration guide](../../recipes/router/calibration/README.md) before performance work. For the requirements, xPyD tuning, and Kueue-specific settings, see the [sidecar guide's TPU7x dynamic-slice tab](../sidecar/README.md#2-deploy-the-model-server).

</details>
<!-- tabs:end -->

Scaling and rollouts work as in the sidecar guide; see [Disaggregated Serving: Operations (DisaggregatedSet)](../../../docs/operations/disaggregation/disaggregatedset.md).

**(Optional) Deploy the monitoring resources for model servers** (see [Collect Metrics](../../../docs/operations/observability/metrics.md)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Gateway

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

### 2. Send Test Requests

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

### 3. Verify P/D disaggregation

A served request only proves the stack is up: a decode pod can also run a request end to end on its own. To confirm that requests are actually split, send a few requests with a long prompt, then check that the prefill pod processed them and the decode pod received their KV cache from it.

**Send 5 requests with a ~1k-token prompt:**

<!-- guide:verify.tests.pd_requests start -->
```bash
# 5 requests with a ~1k-token prompt: each is prefilled on a prefill
# pod, and its KV cache is transferred to a decode pod
kubectl run pd-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 60); do printf "The prefill pod computes the KV cache and the decode pod pulls it to generate tokens. "; done)
    for i in $(seq 1 5); do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question ${i}: what is llm-d?\", \"max_tokens\": 16}"
    done'
```
<!-- guide:verify.tests.pd_requests end -->

**Read the request and KV-transfer counters** of the prefill and decode pods:

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Request and KV-transfer counters of the prefill and decode pods, read
# through the Kubernetes API server proxy (no port-forward needed).
# Both engines serve on port 8000: decode runs no routing sidecar.
for role in prefill decode; do
  for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=${role} -o jsonpath='{.items[*].metadata.name}'); do
    echo "== ${role}: ${pod}"
    kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
      | grep -E '^vllm:(request_success_total|prompt_tokens_total|external_prefix_cache_hits_total|nixl_bytes_transferred_sum)' || true
  done
done
```
<!-- guide:verify.tests.pod_metrics end -->

The prefill pods' `vllm:request_success_total` counts the requests (prefill runs them with `max_tokens=1`). On the decode pods, `vllm:nixl_bytes_transferred_sum` grows with each transfer, and `vllm:external_prefix_cache_hits_total` grows by the prompt tokens that were loaded through the KV transfer connector instead of being recomputed. The 5 test prompts share a ~1k-token prefix, so after the first request decode serves that prefix from its own prefix cache and pulls only the rest.

**Read the Coordinator's per-request log:** each request logs one `pipeline step timings` line with a `prefill` and a `decode` duration.

<!-- guide:verify.tests.coordinator_log start -->
```bash
# One line per request, with the time spent in each pipeline step
kubectl logs -n ${NAMESPACE} deploy/llm-d-coordinator | grep "pipeline step timings"
```
<!-- guide:verify.tests.coordinator_log end -->

If the prefill pods count no requests, check that log, the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`), and that the HTTPRoutes are `Accepted` (`kubectl get httproute -n ${NAMESPACE} -o yaml`). If prefill counts the requests but decode reports no external prefix-cache hits, the KV transfer is failing and decode recomputes the prompt: check the decode pod's logs for NIXL errors.

## Cleanup

To remove the deployed components (`COORDINATOR_OVERLAY` was set in [step 2](#2-deploy-the-coordinator)):

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/pd-disaggregation/coord/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.coordinator start -->
```bash
kustomize build ${REPO_ROOT}/guides/pd-disaggregation/coord/coordinator/${COORDINATOR_OVERLAY}/ | envsubst '${GATEWAY_ADDRESS}' | kubectl delete -n ${NAMESPACE} --ignore-not-found -f -
```
<!-- guide:cleanup.coordinator end -->

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

If nothing else in your cluster still uses it, also remove the `llm-d-inference-gateway` Gateway by following the cleanup steps of your [gateway guide](../../../docs/infrastructure/gateway).
