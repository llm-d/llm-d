# [Experimental] P/D Disaggregation with P2P KV Cache Sharing behind the Coordinator

## Overview

This guide deploys prefill/decode disaggregation through the [Coordinator](../coord-disaggregation/README.md) with the vLLM `OffloadingConnector` P2P tier carrying every KV transfer: the prompt KV from the prefill pod to the decode pod, and cached prefixes from any pod to the pod that computes a prompt.
The Coordinator runs a three-step pipeline, `conditional-decode`, `prefill`, `decode`, on the `kv-offloading` connector.
The model servers run vLLM with the `OffloadingConnector` (a CPU tier and a P2P tier, the same engine setup the [P2P KV Cache Sharing](../p2p-kv-cache-sharing/README.md) guide documents) without a routing sidecar, in two roles. The Coordinator is the one the [Coordinator Disaggregation](../coord-disaggregation/README.md) guide describes, here with a three-step pipeline. All manifests are the guide's own.
For how the pull works and when it pays, see [P2P KV-Cache Sharing](../../docs/architecture/kv-management/p2p-kv-cache-sharing.md).

A request takes this path:

1. The client sends `/v1/completions` or `/v1/chat/completions` to the Gateway. The Coordinator's `HTTPRoute` sends it to the Coordinator.
2. **`conditional-decode`.** The Coordinator sends the request back through the Gateway with `x-llm-d-epp-profile: decode`, `Prefer: if-available` and an empty `kv_transfer_params`. The EPP's `decode` profile picks the decode pod that holds the longest cached prefix of the prompt, else the least busy one. The `prefix-based-pd-decider` compares the prompt against that pod's cache: when fewer than `nonCachedTokens` tokens are missing (a follow-up turn whose conversation the pod already holds), the pod serves the request and the pipeline ends. Otherwise the EPP answers `412 Precondition Failed` and the Coordinator continues.
3. **`prefill`.** The Coordinator sends the prompt with `x-llm-d-epp-profile: prefill` and `kv_transfer_params.remote_decoder`. The EPP's `prefill` profile places it token-aware, on the prefill pod with the fewest prompt tokens still to prefill. The `p2p-source-producer` writes `kv_transfer_params.remote_kv_source` when a peer holds at least `minCachedTokenDelta` more of the prefix than that pod: a decode pod that generated the session's earlier turns and kept them in its CPU tier, or a prefill pod that computed the same document. It also stamps the prefill pod's P2P address on the request, and the `request-header-copy-handler` copies it onto the prefill response. The prefill pod pulls what the source holds, computes the rest, and parks the KV in its CPU tier.
4. **`decode`.** The Coordinator sends the request with `x-llm-d-epp-profile: decode` and `kv_transfer_params.remote_prefiller` built from the copied header. The `decode` profile picks a decode pod, which pulls the prompt KV from the prefill pod's P2P tier and generates. The `p2p-source-producer` writes nothing on this call (`decode-request`): the body already names its source.

The P2P KV Cache Sharing guide ships an aggregated fleet (one role, `OffloadingConnector` only); its README also describes a P/D variant on top of the P/D Disaggregation guide, with NIXL for the handoff. That variant and the Coordinator Disaggregation guide's PD-only pipeline are the two P/D setups this guide compares with:

| | [P2P KV Cache Sharing](../p2p-kv-cache-sharing/README.md), the [P/D variant](../p2p-kv-cache-sharing/README.md#pd-variant-p2p-over-nixl-disaggregation) its README describes | [Coordinator Disaggregation](../coord-disaggregation/README.md), PD-only pipeline | This guide |
| --- | --- | --- | --- |
| Roles | prefill and decode | prefill and decode | prefill and decode |
| Prefill to decode transfer | NIXL (`MultiConnector`) | NIXL (`kv-nixl`) | the P2P tier (`kv-offloading`) |
| Peer pull of a cached prefix | the prefill pod, through the routing sidecar | none | the prefill pod, and a decode pod that serves a probe directly, body written by the EPP |
| Skips prefill when decode holds the conversation | no (`always-disagg` sidecar) | yes (`conditional-decode`) | yes (`conditional-decode`, gated by `prefix-based-pd-decider`) |
| Engine connectors | `NixlConnector` + `OffloadingConnector` | `NixlConnector` | `OffloadingConnector` |
| Per-pod containers | engine and sidecar | engine | engine |

### When to use this path

- **Multi-turn and agentic sessions on P/D.** Each turn's prefill grows with the session; the decode pod that generated the previous turns holds them in its CPU tier, and the prefill pod pulls that history instead of recomputing it. The blog's [session-history measurement](https://llm-d.ai/blog/p2p-kv-cache-sharing-llm-d) is this case.
- **Short follow-up turns should not pay for disaggregation.** `conditional-decode` lets the decode pod that already holds a conversation answer a short new question itself. Set `nonCachedTokens` to the uncached suffix below which a decode pod should compute alone.
- **One connector for both transfers.** The engines run the `OffloadingConnector` only, so a prefill pod and a decode pod are interchangeable peers with identical engine configurations. The P2P KV Cache Sharing guide's [P/D variant](../p2p-kv-cache-sharing/README.md#pd-variant-p2p-over-nixl-disaggregation) keeps NIXL for the prefill to decode transfer instead and needs a routing sidecar per decode pod.
- **Document Q&A on an aggregated fleet** does not need roles: the [P2P KV Cache Sharing](../p2p-kv-cache-sharing/README.md) guide serves it with one role and the routing sidecar.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly):

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `openai/gpt-oss-120b` | 🟡 community | Default. H100/H200 80 GB+ · 2 prefill + 2 decode × TP=1 (4 GPUs) · 160 GiB memory per pod · `INFRA_PROVIDER`: `base` (TCP), `rdma` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

The model server overlays build the decode Deployment on the shared `recipes/` model server base with vLLM `v0.32.0`, `--enable-scale-out` and the `OffloadingConnector` configuration ([`decode/`](modelserver/gpu/vllm/base/decode/kustomization.yaml), [`patch-vllm.yaml`](modelserver/gpu/vllm/base/decode/patch-vllm.yaml)), with no routing sidecar, and derive the prefill Deployment from it with the [`prefill-role`](modelserver/components/prefill-role/kustomization.yaml) component: the same engine configuration (`OffloadingConnector` P2P tier on `7777`, block size 64, `PYTHONHASHSEED=0`), renamed and labeled `llm-d.ai/role: prefill`. Keep the two roles' engine configurations identical, tensor parallelism included: the P2P tier checks a peer fingerprint, and a decode pod at a different TP rejects every cross-role pull.

**Transport (`INFRA_PROVIDER`).** `base` requests no RDMA device, so both transfers run over TCP. `rdma` adds an `rdma/ib` device and `IPC_LOCK` to every model server. See the sidecar guide's [transport notes](../p2p-kv-cache-sharing/README.md#supported-accelerators-and-model-servers).

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide. `kustomize` and `envsubst` are required: the Coordinator and `HTTPRoute` manifests carry placeholders.

- A Gateway provider installed on the cluster. The guide deploys a namespace-scoped Gateway from [`guides/recipes/gateway/`](../recipes/gateway/) for the provider in `GW_PROVIDER_NAME` (`istio` by default; see [the gateway guides](../../docs/infrastructure/gateway)). The EPP's `ext_proc` filter must send response headers to the EPP (`response_header_mode: SEND`): the `request-header-copy-handler` runs on the prefill response. Istio sets this mode itself, as a per-route `ExtProcPerRoute` override on every InferencePool backend; the recipe does not configure it. On another provider, confirm the mode with a config dump from the gateway pod (see [Troubleshooting](#troubleshooting)).

- Ensure your cluster has enough accelerators for your configuration (default: 2 prefill and 2 decode replicas with tensor parallelism 1, 4 GPUs in total, each with enough HBM for `openai/gpt-oss-120b`, e.g. H100 or H200). Each pod also requests 160 GiB of memory for its 88 GiB CPU tier.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell. The router also reads it to reach gated tokenizers.

- The shipped images meet these requirements; check them if you swap images:
  - **Engine**: vLLM `v0.32.0`, pinned by the P2P KV Cache Sharing guide's overlay. It carries the `OffloadingConnector` P2P secondary tier and counts the prompt tokens served from a peer (`vllm:prompt_tokens_cached_by_source_total{source="p2p"}`, [vllm#56318](https://github.com/vllm-project/vllm/pull/56318)).
  - **Router (EPP)**: an image whose `p2p-source-producer` writes `kv_transfer_params.remote_kv_source` into the request body and stamps the prefill pod's P2P address ([llm-d-router#3441](https://github.com/llm-d/llm-d-router/pull/3441)), and that carries the `request-header-copy-handler` ([llm-d-router#3508](https://github.com/llm-d/llm-d-router/pull/3508)). The [router values](router/coord-pd-p2p-kv-cache-sharing.values.yaml) pin `ghcr.io/revit13/llm-d-router-endpoint-picker:dev-kvoff-43`, built from [llm-d-router#3522](https://github.com/llm-d/llm-d-router/pull/3522) (branch `kvoff-4-coordinator-kv-offloading`, which carries #3441 and #3508).
  - **Coordinator**: an image with the `kv-offloading` connector ([llm-d-router#3522](https://github.com/llm-d/llm-d-router/pull/3522)). The [coordinator overlay](coordinator/base/kustomization.yaml) pins `ghcr.io/revit13/llm-d-coordinator:dev-kvoff-43`, built from the same pull request. Upgrade the EPP and the Coordinator together.

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
export GUIDE_NAME=coord-pd-p2p-kv-cache-sharing
export NAMESPACE=llm-d-coord-pd-p2p-kv-cache-sharing
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=base # options: base, rdma
export GW_PROVIDER_NAME=istio # options: istio
export MODEL=openai/gpt-oss-120b # the model the accelerator serves (table above)
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

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../helpers/hf-token.md) matching a valid HuggingFace token:

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

### 1. Deploy a Kubernetes Gateway

The Coordinator's ingress and its outbound calls both go through a Gateway named `llm-d-inference-gateway`. Deploy one in the namespace from the repo recipe for your provider:

<!-- guide:deploy.gateway start -->
```bash
# A namespace-scoped Gateway named llm-d-inference-gateway from the repo recipe
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/gateway/${GW_PROVIDER_NAME}/
kubectl wait -n ${NAMESPACE} --for=condition=Programmed gateway/llm-d-inference-gateway --timeout=5m
```
<!-- guide:deploy.gateway end -->

### 2. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

The [router values](router/coord-pd-p2p-kv-cache-sharing.values.yaml) deploy one EPP replica over one `InferencePool` that holds both roles, with two scheduling profiles selected by the Coordinator's `x-llm-d-epp-profile` header through the `header-profile-handler`. One replica, as in the Coordinator Disaggregation and P/D Disaggregation guides: the `inflight-load-producer`'s in-flight token accounting behind the prefill placement is local to the EPP process, so a second replica would place against half the load.

| Profile | Filter | Placement | Plugins that act on the call |
| --- | --- | --- | --- |
| `prefill` | `prefill-filter` | token-aware: `inflight-load-producer`, `token-load-scorer`, `max-score-picker` | `p2p-source-producer` (`minCachedTokenDelta: 2048`, `prefillProfileName: prefill`) writes the source and the prefill pod's P2P address; `request-header-copy-handler` copies `x-llm-d-kv-cache-prefill-p2p-host-port` onto the response |
| `decode` | `decode-filter` | `prefix-cache-scorer` (weight 2) and `active-request-scorer` (weight 1), `max-score-picker` | `prefix-based-pd-decider` (`nonCachedTokens: 1024`) answers the `conditional-decode` probe; `p2p-source-producer` writes a source on a probe that will be served, and nothing on a decode call that carries `remote_prefiller` |

Both profiles read the same precise prefix index, fed by the engines' KV events from both roles, so a decode pod's CPU tier is visible as a source for a prefill pod. The `InferencePool` targets the engine port `8200`.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in Gateway Mode. The release name `${GUIDE_NAME}` is mandatory: the router `HTTPRoute` below names the `InferencePool` after it:

<!-- guide:deploy.router start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${GW_PROVIDER_NAME} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.router end -->

**Deploy the router's `HTTPRoute`.** The chart's own `HTTPRoute` is disabled (`httpRoute.create: false` in the router values): it would be a catch-all on `/`, which ties with the Coordinator's route on the same Gateway.
Two hand-written routes split the traffic instead. The inference paths without `x-llm-d-epp-profile` go to the Coordinator ([`coordinator/base/httproute.yaml`](coordinator/base/httproute.yaml)).
The same paths with the header (`prefill` or `decode`) go to this router's EPP. Everything else (`/v1/models`, `/health`) goes to the EPP too, which runs its default `decode` profile:

<!-- guide:deploy.httproute start -->
```bash
# The router's HTTPRoute: the Coordinator's x-llm-d-epp-profile calls go to the EPP
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute/base/ \
  | envsubst '${GUIDE_NAME}' | kubectl apply -n ${NAMESPACE} -f -
```
<!-- guide:deploy.httproute end -->

> [!WARNING]
> `x-llm-d-epp-profile` is a plain client-controllable HTTP header, not a trust boundary. A client that sets it on its own request bypasses the Coordinator and reaches the EPP directly. Do not expose this Gateway to untrusted clients without network-level isolation or a provider-specific ingress-level header strip; see the [Coordinator Disaggregation guide](../coord-disaggregation/README.md#1-deploy-the-llm-d-router).

### 3. Deploy the Model Servers

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**With `INFRA_PROVIDER=rdma`, check the RDMA resource name** your nodes expose. The overlay requests `rdma/ib`; yours may differ (`rdma/hca`, `nvidia.com/rdma`, ...), in which case edit [`patch-rdma.yaml`](modelserver/gpu/vllm/rdma/decode/patch-rdma.yaml) to match:

<!-- guide:deploy.rdma_resource start -->
```bash
# only when INFRA_PROVIDER=rdma:
# The RDMA resource your nodes expose (the overlay requests rdma/ib)
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | tr ',' '\n' | grep -i rdma
```
<!-- guide:deploy.rdma_resource end -->

**Apply the Kustomize overlay** for your transport. It creates two Deployments, `coord-pd-p2p-kv-cache-sharing-prefill` and `coord-pd-p2p-kv-cache-sharing-decode`, with the same engine configuration: vLLM with `--block-size=64`, KV-cache events on the per-pod ZMQ socket (port `5556`), `PYTHONHASHSEED=0`, and the `OffloadingConnector` with a CPU tier and a P2P tier on port `7777`. The engines listen on `8200` with no sidecar in front of them:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

**With `INFRA_PROVIDER=rdma`, confirm the device reached the containers** once the pods are running. A pod that schedules without it serves normally and just transfers over TCP, so this failure looks like a performance result rather than a misconfiguration:

<!-- guide:deploy.rdma_device start -->
```bash
# only when INFRA_PROVIDER=rdma:
# The InfiniBand device must be visible inside the model server containers
kubectl exec -n ${NAMESPACE} deploy/coord-pd-p2p-kv-cache-sharing-prefill -c modelserver -- ls /dev/infiniband
kubectl exec -n ${NAMESPACE} deploy/coord-pd-p2p-kv-cache-sharing-decode -c modelserver -- ls /dev/infiniband
```
<!-- guide:deploy.rdma_device end -->

Scale the roles independently with `kubectl scale`. Decode pods bound the number of concurrent sessions and hold their history; prefill pods bound how many long prompts are computed at once. When the fleet passes 16 pods, raise the precise index's `podCacheSize` in the router values (see the P2P KV Cache Sharing guide's [Best Practices](../p2p-kv-cache-sharing/README.md#best-practices)).

### 4. Deploy the Render (Tokenizer) Service

The router's `token-producer` plugin tokenizes each prompt by calling vLLM's `/v1/completions/render` endpoint through the [render Service](render/service.yaml), a Service with no pods of its own that fronts the model servers of both roles:

<!-- guide:deploy.render start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```
<!-- guide:deploy.render end -->

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### 5. Deploy the Coordinator

The [coordinator overlay](coordinator/base/kustomization.yaml) deploys the Coordinator with the pinned image and the pipeline `conditional-decode`, `prefill`, `decode` on the `kv-offloading` connector ([`configmap.yaml`](coordinator/base/configmap.yaml)). Its `gateway.timeout` is `300s`, the `HTTPRoute` request timeout: a prefill call waits in the prefill pod's queue for the whole prompt. `GATEWAY_ADDRESS` is the Gateway's own Service, where the Coordinator sends its calls:

<!-- guide:deploy.coordinator start -->
```bash
# The Gateway's own Service: where the Coordinator sends its prefill and decode calls
export GATEWAY_ADDRESS="http://llm-d-inference-gateway-${GW_PROVIDER_NAME}.${NAMESPACE}.svc:80"
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/base/ \
  | envsubst '${GATEWAY_ADDRESS}' | kubectl apply -n ${NAMESPACE} -f -
kubectl rollout status -n ${NAMESPACE} deployment/llm-d-coordinator --timeout=5m
```
<!-- guide:deploy.coordinator end -->

### 6. (Optional) Tune the Two Thresholds

Two numbers in the [router values](router/coord-pd-p2p-kv-cache-sharing.values.yaml) decide what moves over the P2P tier:

- **`minCachedTokenDelta: 2048`** on the `p2p-source-producer`: the cached-token advantage a source needs before the prefill pod pulls from it instead of recomputing. It is the pull-versus-recompute crossover of the reference setup (gpt-oss-120b on H200 with `rdma/ib`). On any other model, accelerator, or transport, measure your own against the pods you just deployed:

<!-- guide:deploy.calibrate start -->
<!-- llm-d-cicd:skip start -->
```bash
NAMESPACE=${NAMESPACE} \
POD_SELECTOR=llm-d.ai/guide=coord-pd-p2p-kv-cache-sharing \
MODEL_NAME=${MODEL} \
${REPO_ROOT}/guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.calibrate end -->

  The recipe prints the recommended value; set it on the `p2p-source-producer`, re-apply the router release, and restart the EPP. See [Calibrating `minCachedTokenDelta`](../recipes/router/calibration/README.md#calibrating-mincachedtokendelta).

- **`nonCachedTokens: 1024`** on the `prefix-based-pd-decider`: the uncached suffix at which a `conditional-decode` probe is refused and the request goes to prefill. Below it the chosen decode pod computes the suffix itself (a follow-up turn's new question); at or above it, prefill runs (a new document, a long tool result). `0` disables the gate and every probe passes; removing the plugin makes the EPP refuse every probe, so every request runs prefill then decode.

## Verification

### 1. Get the Gateway Address

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

### 2. Check the Render Service

**Check that the render Service returns token IDs for your model.** The response is a JSON list whose first entry carries `token_ids`. This fails on an empty selector, a wrong `targetPort`, or an unavailable render API:

<!-- guide:verify.tests.render start -->
```bash
# The render Service must return token IDs for MODEL before requests are routed
kubectl run render-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://coord-pd-p2p-kv-cache-sharing-render:8000/v1/completions/render" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"render check\", \"max_tokens\": 1}"'
```
<!-- guide:verify.tests.render end -->

### 3. Send a Test Request Twice

**Send the same long completion request twice from a temporary pod inside the cluster.** The first time no decode pod holds the prompt: the probe is refused, prefill computes the prompt, and a decode pod pulls it over the P2P tier. The second time the decode pod that generated the answer holds the prompt and the answer in its CPU tier, so the probe passes and it serves the request alone:

<!-- guide:verify.tests.request start -->
```bash
# The same ~2k-token prompt twice: the first run disaggregates, the second is served by decode alone
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'N=$(date +%s); P=$(for i in $(seq 1 100); do printf "Run ${N}: the prefill pod computes this prompt once and the decode pod serves it alone from then on. "; done)
    for i in 1 2; do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P}\", \"max_tokens\": 64, \"temperature\": 0}"
      sleep 3
    done'
```
<!-- guide:verify.tests.request end -->

**Read the Coordinator's step counters.** It counts one client request per request it served and one upstream call per pipeline step it ran, on its metrics port `9090`:

<!-- guide:verify.tests.coordinator start -->
```bash
# Two client requests; three upstream calls for the first (conditional-decode refused,
# prefill, decode) and one for the second (conditional-decode served)
POD=$(kubectl get pods -n ${NAMESPACE} -l app=llm-d-coordinator -o jsonpath='{.items[0].metadata.name}')
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${POD}:9090/proxy/metrics" \
  | grep -E '^llm_d_coordinator_(request_total|upstream_request_total)' || true
```
<!-- guide:verify.tests.coordinator end -->

What to expect: `request_total` 2, and `upstream_request_total` with 2 `conditional-decode` calls, 1 `prefill` call and 1 `decode` call. The Coordinator's log shows the two paths with their timings: for the first request `cache miss (412), continuing pipeline`, then a `pipeline step timings` line with `prefill` around 2.6 s and `decode` around 0.4 s for this 2,560-token prompt on H200; for the second, `cache hit, response forwarded` and a timings line with only `conditional-decode`, around 0.3 s. The EPP logs the refusal at `ERROR` level (`Failed to process request ... PreconditionFailed - no decode worker has the requested KV cache`); it is the 412 answer the pipeline expects, not a failed request:

<!-- guide:verify.tests.conditional start -->
```bash
# The Coordinator's verdict and step timings for the two requests: a cache miss (412)
# followed by prefill and decode timings, then a cache hit served by conditional-decode alone
kubectl logs -n ${NAMESPACE} deploy/llm-d-coordinator | grep -E 'cache miss \(412\)|cache hit|pipeline step timings' | tail -4 \
  | while read -r line; do echo "${line}" | grep -oE '"(body|conditional-decode|prefill|decode)":"[^"]*"' | tr '\n' ' '; echo; done
```
<!-- guide:verify.tests.conditional end -->

### 4. Verify the Prefill to Decode Transfer

The first request's decode pod served its prompt from the prefill pod's P2P tier. vLLM counts those prompt tokens under `source="p2p"` on the decode pods, and the prefill pod's engine logged the decode pod's inbound P2P session:

<!-- guide:verify.tests.pd_transfer start -->
```bash
# Prompt tokens the decode pods served from a peer's P2P tier (the prefill pod's), and the sessions
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[*].metadata.name}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8200/proxy/metrics" \
    | grep -E '^vllm:prompt_tokens_cached_by_source_total\{.*source="p2p"' || true
done
kubectl logs -n ${NAMESPACE} -l llm-d.ai/role=prefill -c modelserver --tail=-1 | grep -c 'accepting INBOUND connection'
```
<!-- guide:verify.tests.pd_transfer end -->

What to expect: one decode pod reports the prompt length under `source="p2p"` (2,560 tokens for this prompt, whole 64-token blocks), and at least one prefill pod accepted an inbound session.

### 5. Verify the Cross-Role Pull

A source pull in this topology runs from a decode pod to a prefill pod. Confirm the engines accept it regardless of role: compute a fresh prefix on a decode pod (the source), then ask a prefill pod, which has never seen it, to pull it. Both requests go straight to the engines (port `8200`); the second carries the same `kv_transfer_params.remote_kv_source` the EPP writes when it names a source:

<!-- guide:verify.tests.p2p_pull start -->
```bash
# Compute a new ~2k-token prefix on a decode pod (the source), then send it
# to a prefill pod (the consumer), naming the source in kv_transfer_params
SOURCE=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].status.podIP}')
CONSUMER_POD=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=prefill -o jsonpath='{.items[0].metadata.name}')
CONSUMER=$(kubectl get pod -n ${NAMESPACE} ${CONSUMER_POD} -o jsonpath='{.status.podIP}')
kubectl run p2p-pull-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="SOURCE=${SOURCE}" \
  --env="CONSUMER=${CONSUMER}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'N=$(date +%s); P=$(for i in $(seq 1 100); do printf "Run ${N}: the prefill pod pulls this prefix from the CPU tier of the decode pod that computed it. "; done)
    curl -sS -o /dev/null -w "source ${SOURCE}: HTTP %{http_code}\n" -X POST "http://${SOURCE}:8200/v1/completions" \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P}\", \"max_tokens\": 1}"
    sleep 5
    curl -sS -o /dev/null -w "consumer ${CONSUMER}: HTTP %{http_code}\n" -X POST "http://${CONSUMER}:8200/v1/completions" \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P}\", \"max_tokens\": 1, \"kv_transfer_params\": {\"remote_kv_source\": {\"kv_request_id\": \"p2p-check-${N}\", \"remote_host\": \"${SOURCE}\", \"remote_port\": 7777}}}"'
```
<!-- guide:verify.tests.p2p_pull end -->

**Read the consumer's external prefix-cache counters:**

<!-- guide:verify.tests.pull_metrics start -->
```bash
# External prefix-cache counters of the consumer (prefill) pod, read through the
# Kubernetes API server proxy (no port-forward needed)
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${CONSUMER_POD}:8200/proxy/metrics" \
  | grep -E '^vllm:external_prefix_cache_(hits|queries)_total' || true
```
<!-- guide:verify.tests.pull_metrics end -->

What to expect: both requests return HTTP 200, and `vllm:external_prefix_cache_hits_total` on the consumer is well above zero (the prefix length in whole 64-token blocks, 2,560 for this prompt). If it stays at zero and the source logged `rejecting peer`, the two roles' engine configurations differ; see [Troubleshooting](#troubleshooting).

### 6. Verify Router-Driven Pulls

The check above bypasses the router. A router-driven source pull needs a prompt whose prefix a peer holds and the chosen prefill pod does not, by `minCachedTokenDelta` or more: in a multi-turn session, the previous turns' history that the decode pod kept. Run a multi-turn load with long shared prefixes and more concurrent sessions than decode pods (the document Q&A profile of the P2P KV Cache Sharing guide, [llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656), is one), then read the EPP's decisions by profile. On prefill calls, `pulled` lines name the source pod (`best`), the prefill pod (`computing`) and the token gain (`deltaTokens`); on decode calls after prefill the outcome is `decode-request`:

<!-- guide:verify.tests.pull_evidence start -->
```bash
# Router-driven pulls, after load: the EPP's decision per call, and per pod the prompt
# tokens vLLM served from a peer's P2P tier (on prefill pods: source pulls; on decode
# pods: the prompt KV pulled from prefill pods, plus any pull on a probe served directly)
kubectl logs -n ${NAMESPACE} -l llm-d-router-gateway=${GUIDE_NAME}-epp --tail=-1 \
  | grep '"p2p decision"' | grep -oE '"outcome":"[a-z-]+"' | sort | uniq -c
for role in prefill decode; do
  for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=${role} -o jsonpath='{.items[*].metadata.name}'); do
    echo "== ${role} ${pod}"
    kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8200/proxy/metrics" \
      | grep -E '^vllm:prompt_tokens_cached_by_source_total\{.*source="p2p"' || true
  done
done
```
<!-- guide:verify.tests.pull_evidence end -->

What to expect: a non-zero `pulled` count and a non-zero `source="p2p"` counter on the prefill pods. The other outcomes are expected too: `self` when the chosen pod is the best source, `below-floor` when the gain is under `minCachedTokenDelta`, `no-best-match` when no peer holds the prefix, `decode-request` on every decode call that carries `remote_prefiller`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| No request ever runs prefill, the Coordinator never logs `cache miss (412)`, and the EPP logs `token-producer render call failed` and `conditional-decode: error evaluating gate, forwarding` | the render Service is missing or has no endpoints, so prompts are not tokenized; the decider cannot evaluate the gate and lets every probe through, and the decode pods compute every prompt themselves | deploy the [render Service](render/service.yaml) (step 4) and check it with the render verification step; its selector must match the model servers' guide label |
| Every request runs prefill then decode; the Coordinator logs `cache miss (412)` for every request, including repeats | the `prefix-based-pd-decider` is missing from the EPP config, so the EPP refuses every `Prefer: if-available` probe, or `nonCachedTokens` is too low for the follow-up turns | keep the decider in the [router values](router/coord-pd-p2p-kv-cache-sharing.values.yaml); raise `nonCachedTokens` above the new tokens a follow-up turn adds |
| No request ever runs prefill; the Coordinator never logs `cache miss (412)` | `nonCachedTokens: 0` disables the gate, so every probe passes and the decode pods compute every prompt | set `nonCachedTokens` to the uncached suffix that should go to prefill |
| Prefill calls fail with HTTP 500 and the Coordinator logs a missing or malformed `x-llm-d-kv-cache-prefill-p2p-host-port` | the EPP did not stamp the prefill pod's P2P address, or the `request-header-copy-handler` did not copy it onto the response (plugin missing, or the Gateway's `ext_proc` filter does not send response headers to the EPP) | use the pinned EPP image, keep the handler in the router values, and check the mode the provider generated: `kubectl exec -n ${NAMESPACE} deploy/llm-d-inference-gateway-istio -c istio-proxy -- curl -s localhost:15000/config_dump \| grep -A6 '"processing_mode"'` must show `"response_header_mode": "SEND"` on the route override (the listener-level filter is a placeholder with `SKIP`) |
| The decode pods' `source="p2p"` counters stay at zero while prefill ran | the decode pod could not pull from the prefill pod: a peer fingerprint mismatch (`rejecting peer` in the prefill pod's log) from different engine configurations across roles, or the P2P port is unreachable | keep both roles on the same engine arguments and tensor parallelism (the [`prefill-role`](modelserver/components/prefill-role/kustomization.yaml) component changes names and labels only); check the `7777` listener |
| The EPP log carries one `Failed to process request ... PreconditionFailed` line at `ERROR` level per refused probe | the EPP reports the `412 Precondition Failed` it returns to a `conditional-decode` probe as a request error; the Coordinator continues the pipeline | nothing to fix; count them against the Coordinator's `cache miss (412)` lines, which match one to one |
| Every request answers 404 or loops between the Gateway and the Coordinator | the router `HTTPRoute` matches a different header name than the Coordinator sends | deploy [`router/httproute/base`](router/httproute/base/kustomization.yaml), whose matches carry `x-llm-d-epp-profile`, and keep the EPP and Coordinator images from the same branch |
| The Coordinator fails at startup on an unknown `kv_connector` or step | the coordinator image predates the `kv-offloading` connector or `conditional-decode` | use the image pinned in [`coordinator/base/kustomization.yaml`](coordinator/base/kustomization.yaml) |
| Prefill calls fail after 60 s under load | an older Coordinator config with `gateway.timeout: 60s`; a queued long prompt outlives it | this guide's [`configmap.yaml`](coordinator/base/configmap.yaml) sets `300s`, the `HTTPRoute` request timeout |
| EPP logs `bestCachedTokens:0` for every request | the KV-events topic names a port the router does not identify the endpoint by, so the index stays empty | keep `POD_PORT` at `8200` in [`patch-vllm.yaml`](modelserver/gpu/vllm/base/decode/patch-vllm.yaml), the engine port the `InferencePool` targets |
| Engine-side symptoms (`rejecting peer`, block-size mismatch, zero hit rate, render timeouts) | as in the sidecar deployment | the P2P KV Cache Sharing guide's [Troubleshooting](../p2p-kv-cache-sharing/README.md#troubleshooting) table |

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.coordinator start -->
```bash
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/base/ \
  | envsubst '${GATEWAY_ADDRESS}' | kubectl delete -n ${NAMESPACE} --ignore-not-found=true -f -
```
<!-- guide:cleanup.coordinator end -->

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.render start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```
<!-- guide:cleanup.render end -->

<!-- guide:cleanup.rest start -->
```bash
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute/base/ \
  | envsubst '${GUIDE_NAME}' | kubectl delete -n ${NAMESPACE} --ignore-not-found=true -f -

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/gateway/${GW_PROVIDER_NAME}/ --ignore-not-found=true

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
