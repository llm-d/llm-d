# Coordinator Disaggregation (Encode / Prefill / Decode)

## Overview

> [!WARNING]
> This is an **experimental architecture**. It is less mature and less tested than
> [P/D Disaggregation](../pd-disaggregation/README.md), and may change in upcoming
> releases.

This guide deploys a standalone **Coordinator** service in front of an Encode /
Prefill / Decode (EPD) topology. Instead of the per decode pod [Routing
Sidecar](../../docs/architecture/advanced/disaggregation/README.md) that today's
[P/D Disaggregation](../pd-disaggregation/README.md) guide uses to dispatch a fixed
prefill→decode sequence, the Coordinator is a single service that drives a
**configurable pipeline** over each request:

```
replace-media-urls → render → conditional-decode → encode → prefill → decode
```

Every call the Coordinator makes for a phase (`conditional-decode`, `encode`, `prefill`,
`decode`) goes through the same Gateway and the same EPP, which picks the pod for that
phase via the [Endpoint Picker protocol](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/docs/proposals/004-endpoint-picker-protocol)
(`ext_proc`). `conditional-decode` tries decode first, optimistically, before running
encode or prefill at all: if the chosen decode pod already has what it needs (e.g. the
prompt is already cached), it serves the request directly and the full pipeline never
runs. Only if decode responds `412 Precondition Failed` does the Coordinator fall back
to the full `encode → prefill → decode` cascade — which is also how a request with
multiple multimedia entries fans encode out in parallel, one call per entry.

See the [Coordinator architecture doc](https://github.com/llm-d/llm-d-router/blob/main/docs/coordinator_architecture.md)
for the full request-flow sequence diagram and design rationale.

This is the experimental part of the architecture: the Coordinator is a candidate to
**replace the routing sidecar**, and it changes two things about how requests are
orchestrated:

* **Modularity** — the pipeline is a plain list of named steps in the Coordinator's
  `ConfigMap` (see [`coordinator/configmap.yaml`](coordinator/configmap.yaml)). Steps
  can be added, removed, or reordered by editing that list — no code changes, no
  rebuilding an image. The [PD-only note](#installation-instructions) below is a
  worked example: dropping the `replace-media-urls`, `render`, and `encode` steps is
  all it takes to turn this into a text-only P/D deployment.
* **Deferred decoding** — with the sidecar, the EPP picks the encode, prefill, *and*
  decode pods together in one scheduling cycle, before the request has even reached
  encode or prefill. By the time decode actually starts, whatever made that decode pod
  look best (queue depth, KV cache state, in-flight load) may no longer hold, so the
  pre-picked pod can be a stale, sub-optimal choice. The Coordinator only calls the EPP
  for a phase when that phase is actually about to run, so the decode pod is selected
  after encode/prefill have already completed — on the pool's current state, not a
  snapshot taken one or more phases earlier.


The result of this guide is a combination of two independent choices you make in
[Installation Instructions](#installation-instructions): which of two **EPP
topologies** to run, and whether to run the full **EPD** pipeline or drop the
`encode` role for a **PD-only** deployment (see the PD-only note there). The two
choices don't interact — any EPP topology works with either pipeline scope. The EPP
topology choice:

* **Either: 1 Endpoint Picker (EPP)** covering all three roles, and **1 InferencePool**
  spanning them. The EPP runs one scheduling profile per call — `encode`, `prefill`,
  or `decode` — selected by the Coordinator's `EPP-Profile` header via the
  [header-profile-handler](https://github.com/llm-d/llm-d-router/blob/main/pkg/epp/framework/plugins/scheduling/profilehandler/headerprofile/README.md)
  plugin. Each profile filters the shared pool down to its own role with a by-label filter.
* **Or: 3 EPPs**, one per role, each with its own **InferencePool** scoped to that
  role's pods via `modelServers.matchLabels`. Each EPP runs a single `default`
  scheduling profile picked implicitly by the chart's `single-profile-handler`
  (no profile-selection plugin needed, since a role-scoped EPP never has to
  choose between profiles) — no custom image either. Each role's file does
  configure its own scorer plugins (see below); that's just picking which
  endpoint within the role, not which profile to run. The Gateway's `HTTPRoute`
  (not the EPP) is what dispatches each `EPP-Profile` value to the right EPP's
  InferencePool.

## Default Configuration

| Parameter          | Value                                                              |
| ------------------ | ------------------------------------------------------------------ |
| Model              | [Qwen/Qwen3-VL-32B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct) |
| Roles              | encode, prefill, decode                                            |
| Replicas per role  | encode: 2, prefill: 4, decode: 4 (encode: 0 in the PD-only deployment) |
| Tensor Parallelism | 2                                                                   |
| GPUs per replica   | 2                                                                   |
| Total GPUs         | 20                                                                  |

### Supported Hardware Backends

| Backend           | Directory                | Notes                                            |
| ------------------ | ------------------------- | ------------------------------------------------- |
| NVIDIA GPU (vLLM) | `modelserver/gpu/vllm/`  | Default configuration (`base`, `coreweave`, and `gke` providers) |

> [!NOTE]
> Encoder-cache transfer (`--ec-transfer-config`) is not yet in an official vLLM
> release, so the model server manifests pin a dev build
> (`ghcr.io/revit13/vllm-openai`) — the same one the
> [Encode Disaggregation guide](../multimodal-serving/e-disaggregation/README.md)
> uses. Swap the pinned tag once encoder-cache transfer lands upstream.

## Prerequisites

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
  export GUIDE_NAME="coord-disaggregation"
  export NAMESPACE="llm-d-coord-disaggregation"
  export MODEL_NAME="Qwen/Qwen3-VL-32B-Instruct"
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```

* Create a target namespace for the installation:

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

> [!NOTE]
> The steps below deploy the full **EPD** topology. For a **PD-only** deployment (no
> `encode` role), this is where the modularity described in the [Overview](#overview)
> pays off:
> * Step 1: no change needed — the same single Router/EPP deployment serves whichever
>   roles you actually run; an `encode` scheduling profile with no `encode`-labeled pods
>   behind it is simply never called, since the Coordinator's pipeline (step 3) is what
>   decides whether an `encode` phase call happens at all.
> * Step 2: skip the encode model server overlay, or scale it to 0 replicas.
> * Step 3: after deploying the Coordinator, apply
>   [`coordinator/patch-pd-only.yaml`](coordinator/patch-pd-only.yaml) to drop the
>   `replace-media-urls`, `render`, and `encode` steps from `pipeline.steps` (keeping
>   only `conditional-decode`, `prefill`, and `decode`), then restart the coordinator
>   Deployment:
>   ```bash
>   kubectl patch configmap llm-d-coordinator-config -n ${NAMESPACE} --type=strategic \
>       --patch="$(envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/patch-pd-only.yaml)"
>   kubectl rollout restart deployment/llm-d-coordinator -n ${NAMESPACE}
>   ```
> * Step 4: skip entirely — the multimedia downloader is only used by the `replace-media-urls` pipeline step.

### 1. Deploy the llm-d Router

Pick **one** of the two topologies from the [Overview](#overview) — single EPP
(default below) or 3 separate EPPs (in the collapsed section further down). Don't do
both; they install into the same namespace and would conflict.

Both topologies default to routing the Coordinator's own ingress
(`coordinator/httproute.yaml`) and its outbound `gateway.address`
([`coordinator/configmap.yaml`](coordinator/configmap.yaml)) through a real
Kubernetes Gateway. The single-EPP topology also has a Standalone Mode (further
down, no Gateway API `Gateway`/`HTTPRoute` at all) — see that section for why only
single-EPP supports it. For Gateway mode, deploy one first if your cluster doesn't
already have one:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster. Skip this step if you're using the single-EPP topology's Standalone Mode instead.

#### Single EPP (default)

One llm-d Router release, EPP, and InferencePool cover all three roles.

2. *Deploy the llm-d Router*:

```bash
export PROVIDER_NAME=gke # other: na, agentgateway, or istio
export GATEWAY_SERVICE=llm-d-inference-gateway-${PROVIDER_NAME}
export CLIENT_SERVICE=${GATEWAY_SERVICE} # client-facing entrypoint: the Gateway's own Service
export CLIENT_PORT=80
export ROUTER_RELEASES=${GUIDE_NAME} # Helm release name(s), for Cleanup
export ROUTER_HTTPROUTE_FILE=router/httproute.yaml # for Cleanup
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

3. *Deploy the Router's HTTPRoute*. The chart's own auto-created HTTPRoute is disabled
   (`httpRoute.create: false` in [`router/coord-disaggregation.values.yaml`](router/coord-disaggregation.values.yaml))
   because it would be an unconditional catch-all on `/`, colliding with the
   Coordinator's own route on the same Gateway. Instead, the two hand-authored
   HTTPRoutes on this Gateway (`coordinator/httproute.yaml` and
   [`router/httproute.yaml`](router/httproute.yaml)) split traffic three ways:
   - `/v1/completions`, `/v1/chat/completions`, `/inference/v1/generate` **without**
     `EPP-Profile` → the Coordinator (client-facing inference calls).
   - The same three paths **with** `EPP-Profile` → this router's EPP (the Coordinator's
     own internal per-phase calls reuse those same paths, so both HTTPRoutes match
     them at the same exact-path specificity; the header match then breaks the tie in
     the router's favor — see the comments in both files for why path specificity
     must match for this to work).
   - Everything else without `EPP-Profile` (e.g. `/v1/models`, `/health`) → this
     router's EPP too, which already falls back to its `decode` scheduling profile
     when the header is absent.

> [!WARNING]
> `EPP-Profile` is a plain client-controllable HTTP header, not a trust boundary. A
> client that forges it on its own request bypasses the Coordinator's pipeline
> entirely, matching the router's HTTPRoute directly instead. There is no portable
> Gateway API mechanism to strip or verify it before routing decisions are made —
> route filters like `RequestHeaderModifier` only apply *after* a rule has already
> matched, so they can't close this. Acceptable for this guide's experimental,
> small-scale scope; don't expose this Gateway to untrusted clients without adding
> network-level isolation (mTLS peer identity, `NetworkPolicy`) or a provider-specific
> ingress-level header strip (e.g. an Istio `EnvoyFilter`) first.

```bash
envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute.yaml | kubectl apply -n ${NAMESPACE} -f -
```

<details>
<summary><h4>Standalone Mode (single EPP only, no Kubernetes Gateway)</h4></summary>

Deploys the llm-d Router with its own Envoy sidecar and Service — no Gateway API
`Gateway`/`HTTPRoute` involved at all. This works for the single-EPP topology because
the Coordinator's Service and the router-EPP's Service become two independent
ClusterIPs instead of both being routed through one shared Gateway listener: the
`Exact`-path + `EPP-Profile` header tie-break that Gateway mode's two hand-authored
HTTPRoutes rely on (see above) exists only to disambiguate two things sharing that one
listener. With no shared listener, there's nothing left to disambiguate — the
Coordinator's outbound calls just go straight to the router-EPP's own Service.

**Not available for the 3-EPP topology**: the Coordinator has a single
`gateway.address` ([`coordinator/configmap.yaml`](coordinator/configmap.yaml)), not one
per role. In Gateway mode, [`router/httproute-3-epp.yaml`](router/httproute-3-epp.yaml)
is what fans that one address out to the right one of three InferencePools by
`EPP-Profile` header. There's no standalone equivalent of that fan-out, so 3-EPP still
needs a real Gateway.

2. *Deploy the llm-d Router*:

```bash
export GATEWAY_SERVICE=${GUIDE_NAME}-epp
export CLIENT_SERVICE=llm-d-coordinator # client-facing entrypoint: the Coordinator's own Service
export CLIENT_PORT=8080
export ROUTER_RELEASES=${GUIDE_NAME} # Helm release name(s), for Cleanup
export ROUTER_HTTPROUTE_FILE= # no HTTPRoute in Standalone Mode, for Cleanup
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

No HTTPRoute to apply here, and no Gateway to deploy beforehand (skip step 1.1 above
entirely) — the router-EPP's own Service, named `${GATEWAY_SERVICE}` (the chart derives
it from the Helm release name), is what the Coordinator's `gateway.address` points at
directly once you deploy it in step 3.

</details>

<details>
<summary><h4>3 separate EPPs (one per role)</h4></summary>

Three independent llm-d Router releases — one per role — each with its own EPP and
InferencePool scoped to that role's pods. No profile-selection plugin needed: each
EPP only ever sees one role, so the chart's default `single-profile-handler` picks its
one configured profile automatically (see the [Overview](#overview)).

`router/coord-disaggregation-prefill.values.yaml` and
`router/coord-disaggregation-decode.values.yaml` configure the same scorers as the
`prefill`/`decode` profiles in
[`guides/pd-disaggregation/router/pd-disaggregation.values.yaml`](../pd-disaggregation/router/pd-disaggregation.values.yaml):
`prefix-cache-affinity-filter` + `token-load-scorer` for prefill (stay on cache-warm
pods, then pick by queued token load), `active-request-scorer` for decode (pick the
least-busy endpoint) — minus the `prefill-filter`/`decode-filter`/`disagg-*` plugins
that guide needs to split one shared pool, which this guide's `modelServers.matchLabels`
already does per-release. `router/coord-disaggregation-encode.values.yaml` has no
`prefill`/`decode` profile to borrow from in pd-disaggregation, so it reuses
`active-request-scorer` too (pick the least-busy encode pod) — encode has no
prefix-cache affinity to speak of, just queue/load balancing.

2. *Deploy the llm-d Routers*:

```bash
export PROVIDER_NAME=gke # other: na, agentgateway, or istio
export GATEWAY_SERVICE=llm-d-inference-gateway-${PROVIDER_NAME}
export CLIENT_SERVICE=${GATEWAY_SERVICE} # client-facing entrypoint: the Gateway's own Service
export CLIENT_PORT=80
export ROUTER_RELEASES="${GUIDE_NAME}-encode ${GUIDE_NAME}-prefill ${GUIDE_NAME}-decode" # for Cleanup
export ROUTER_HTTPROUTE_FILE=router/httproute-3-epp.yaml # for Cleanup
for ROLE in encode prefill decode; do
  helm install ${GUIDE_NAME}-${ROLE} \
      ${ROUTER_GATEWAY_CHART} \
      -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
      -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
      -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}-${ROLE}.values.yaml \
      --set provider.name=${PROVIDER_NAME} \
      -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
done
```

3. *Deploy the shared HTTPRoute*. Same reasoning as the single-EPP variant's
   `httpRoute.create: false` (each release disables its own auto-created HTTPRoute for
   the same specificity-tie reason — see the comment in
   [`router/httproute-3-epp.yaml`](router/httproute-3-epp.yaml)), but instead of one
   shared backend, [`router/httproute-3-epp.yaml`](router/httproute-3-epp.yaml) routes
   each `EPP-Profile` value to its own role's InferencePool (`${GUIDE_NAME}-encode`,
   `${GUIDE_NAME}-prefill`, `${GUIDE_NAME}-decode` — the InferencePool name matches the
   Helm release name). The same [!WARNING] about `EPP-Profile` not being a trust
   boundary applies here too.

```bash
envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute-3-epp.yaml | kubectl apply -n ${NAMESPACE} -f -
```

</details>

### 2. Deploy the Model Servers

Apply the Kustomize overlay for your infrastructure provider. One overlay deploys all
three role-specific model servers (encode, prefill, decode), each as a single
replica:

```bash
export INFRA_PROVIDER=base # base | coreweave | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
```

> [!NOTE]
> Each model server pod (and the Coordinator's render container, deployed next) pulls
> its own copy of the model from the HuggingFace Hub independently — there's no shared
> model cache between them, matching the default convention used by other guides in
> this repo (e.g. [P/D Disaggregation](../pd-disaggregation/README.md)). Expect the
> first cold start to take a while on every pod, not just one; if that's a problem in
> your cluster (slow/metered egress, many replicas), add an RWX-backed
> `PersistentVolumeClaim` mounted at a shared `HF_HOME` path across these manifests and
> the Coordinator's `vllm-render` container instead.

### 3. Deploy the Coordinator

Drives the `replace-media-urls → render → conditional-decode → encode → prefill →
decode` pipeline. The ConfigMap references `${NAMESPACE}` and `${GATEWAY_SERVICE}`
(exported in step 1's Gateway mode or Standalone Mode block — whichever you used), so
build with `kustomize` and pipe through `envsubst` before applying:

```bash
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/ | envsubst | kubectl apply -n ${NAMESPACE} -f -
```

> [!NOTE]
> Using Standalone Mode from step 1? `coordinator/kustomization.yaml` bundles
> [`coordinator/httproute.yaml`](coordinator/httproute.yaml) unconditionally, but
> Standalone Mode has no Gateway for it to attach to. Apply the ConfigMap and
> Deployment directly instead, skipping that one resource:
> ```bash
> envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/configmap.yaml | kubectl apply -n ${NAMESPACE} -f -
> kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/deployment.yaml
> ```

### 4. (Optional) Deploy the multimedia downloader (caching proxy)

The Coordinator's `replace-media-urls` step can route outbound media fetches through
an in-cluster forward proxy (e.g. Squid) that caches origin images/video, eliminating
redundant fetches across requests. Caching HTTPS origins requires the proxy to
terminate TLS and re-sign responses with its own CA (SSL-Bump), which means the
Coordinator needs to trust that CA.

This repo doesn't ship a caching proxy of its own — deploy one for your cluster (e.g.
an SSL-Bump-configured Squid), then trust its CA in the Coordinator with
[`multimedia-downloader/patch-coordinator-ca.yaml`](multimedia-downloader/patch-coordinator-ca.yaml).
This requires the Coordinator from step 3 to already be deployed:

```bash
kubectl patch deployment llm-d-coordinator -n ${NAMESPACE} \
    --type=strategic --patch-file ${REPO_ROOT}/guides/${GUIDE_NAME}/multimedia-downloader/patch-coordinator-ca.yaml
kubectl rollout restart deployment/llm-d-coordinator -n ${NAMESPACE}
```

Without this step, `replace-media-urls` still works — it just fetches media directly
instead of through a cache.

### 5. (Optional) Enable monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).

## Verification

### 1. Get the IP of the Entrypoint

Same command either way — query the ClusterIP of whichever Service is client-facing.
`${CLIENT_SERVICE}`/`${CLIENT_PORT}` were exported in step 1: the Gateway's own Service
in Gateway mode (test clients only ever run in-cluster anyway, so there's no need to
resolve the Gateway resource's external address specifically — its own Service's
ClusterIP gets you there just as well), or the Coordinator's own Service in Standalone
Mode (there's no Gateway at all, so clients hit the Coordinator directly instead of the
router-EPP's Service, which in that mode is Coordinator-internal only — see step 1).

```bash
export IP=$(kubectl get service ${CLIENT_SERVICE} -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
export PORT=${CLIENT_PORT}
```

In Gateway mode, the Coordinator's own `coordinator` HTTPRoute (deployed alongside the
coordinator overlay in step 3) attaches to the `llm-d-inference-gateway` Gateway and
forwards client traffic (no `EPP-Profile` header) to the Coordinator, which then
orchestrates the `encode → prefill → decode` pipeline. The EPP is internal — the
Coordinator selects which of its scheduling profiles runs by setting the `EPP-Profile`
header on its own outbound calls, which route back through that same Gateway Service to
`router/httproute.yaml` (step 1.3) instead of the Coordinator's route.

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="PORT=$PORT" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}:${PORT}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-VL-32B-Instruct",
        "prompt": "How are you today?"
    }' | jq
```

This text-only prompt takes the fast path described in [Deferred decoding](#overview):
`conditional-decode` serves it directly, so `encode` and `prefill` never get called.

**Send a multimodal completion request** to exercise the full `encode → prefill →
decode` pipeline:

```bash
curl -X POST http://${IP}:${PORT}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-VL-32B-Instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://images.dog.ceo/breeds/retriever-golden/n02099601_3004.jpg"
                        }
                    },
                    {
                        "type": "text",
                        "text": "What is in this image?"
                    }
                ]
            }
        ],
        "max_tokens": 128
    }' | jq
```

## Cleanup

Same commands regardless of topology or mode — `${ROUTER_RELEASES}` and
`${ROUTER_HTTPROUTE_FILE}` were exported in step 1 (empty in Standalone Mode, since no
HTTPRoute was created there). The Coordinator's resources are deleted directly rather
than via `coordinator/kustomization.yaml`'s bundling (same reasoning as step 3's
apply-side note) — `--ignore-not-found` makes that safe regardless of whether
`coordinator/httproute.yaml` was ever applied:

```bash
for RELEASE in $(echo ${ROUTER_RELEASES}); do
  helm uninstall ${RELEASE} -n ${NAMESPACE}
done
if [ -n "${ROUTER_HTTPROUTE_FILE}" ]; then
  envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/${ROUTER_HTTPROUTE_FILE} | kubectl delete -n ${NAMESPACE} -f -
fi

kubectl delete -n ${NAMESPACE} --ignore-not-found -f ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/httproute.yaml
kubectl delete -n ${NAMESPACE} --ignore-not-found -f ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/deployment.yaml
envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/configmap.yaml | kubectl delete -n ${NAMESPACE} --ignore-not-found -f -

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

This deletes every resource the guide created, but leaves the namespace itself (and
anything else in it, like the `llm-d-hf-token` secret) alone — `kubectl delete
namespace ${NAMESPACE}` if you want it gone entirely.

If you used Gateway mode and nothing else in your cluster still uses it, also remove
the `llm-d-inference-gateway` Gateway by following [the gateway cleanup guide](../../docs/infrastructure/gateway/gke.md#cleanup).
Standalone Mode never created one.

