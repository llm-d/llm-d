# Coordinator Disaggregation (Encode / Prefill / Decode)

## Overview

> [!WARNING]
> This is an **experimental architecture**. It is validated at small scale and is not
> yet part of the nightly E2E CI that covers guides like [P/D Disaggregation](../pd-disaggregation/README.md).

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
multiple multimedia entries fans encode out in parallel, one call per entry. See the
[Coordinator architecture doc](https://github.com/llm-d/llm-d-router/blob/main/docs/coordinator_architecture.md)
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

The vLLM-level mechanics of KV and encoder-cache transfer are unchanged from standard
disaggregated serving (NIXL, `kv-transfer-config` / `ec-transfer-config`) — only the
*orchestration* of when each phase runs moves out of the per-pod sidecar and into the
Coordinator.

The result of this guide:

* **1 Coordinator**, terminating client traffic and driving the pipeline.
* **1 Endpoint Picker (EPP)** covering all three roles, and **1 InferencePool**
  spanning them. The EPP runs one scheduling profile per call — `encode`, `prefill`,
  or `decode` — selected by the Coordinator's `EPP-Profile` header via the
  `header-profile-handler` plugin
  ([llm-d-router#2134](https://github.com/llm-d/llm-d-router/pull/2134)); each profile
  filters the shared pool down to its own role with a by-label filter.
* **1 vLLM replica per role** — three model servers in total, distinguished only by
  their `llm-d.ai/role` label.

> [!IMPORTANT]
> The single-EPP setup below depends on
> [llm-d-router#2134](https://github.com/llm-d/llm-d-router/pull/2134)
> (`header-profile-handler` plus the `encode-filter`/`prefill-filter`/`decode-filter`
> plugins), which is merged but may not be in an official release image yet. Until it
> is, [`router/coord-disaggregation.values.yaml`](router/coord-disaggregation.values.yaml)
> pins `router.epp.image` to `ghcr.io/roytman/llm-d-router-endpoint-picker:1-epp`, a
> build that contains it. Swap it back to the chart default once an official image does.

## Default Configuration

| Parameter          | Value                                                              |
| ------------------ | ------------------------------------------------------------------ |
| Model              | [Qwen/Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) |
| Roles              | encode, prefill, decode                                            |
| Replicas per role  | 1                                                                   |
| Tensor Parallelism | 1                                                                   |
| GPUs per replica   | 1                                                                   |
| Total GPUs         | 3                                                                   |

### Supported Hardware Backends

| Backend           | Directory                | Notes                                            |
| ------------------ | ------------------------- | ------------------------------------------------- |
| NVIDIA GPU (vLLM) | `modelserver/gpu/vllm/`  | Default configuration (`base` and `gke` providers) |

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
  export MODEL_NAME="Qwen/Qwen3-VL-2B-Instruct"
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
>
> * Step 1: no change needed — the same single Router/EPP deployment serves whichever
>   roles you actually run; an `encode` scheduling profile with no `encode`-labeled pods
>   behind it is simply never called, since the Coordinator's pipeline (step 4) is what
>   decides whether an `encode` phase call happens at all.
> * Step 3: skip the encode model server overlay, or scale it to 0 replicas.
> * Step 4: after deploying the Coordinator, edit the `llm-d-coordinator-config`
>   ConfigMap to drop the `replace-media-urls`, `render`, and `encode` steps from
>   `pipeline.steps`, keeping only `conditional-decode`, `prefill`, and `decode`, then
>   restart the coordinator Deployment.
> * Step 5: skip entirely — the multimedia downloader is only used by the `replace-media-urls` pipeline step.

### 1. Deploy the llm-d Router

One llm-d Router release, EPP, and InferencePool cover all three roles — see the
[Overview](#overview) for why this is a single deployment rather than one per role.

The Coordinator's own ingress (`coordinator/httproute.yaml`) and its outbound
`gateway.address` ([`coordinator/configmap.yaml`](coordinator/configmap.yaml)) both
always route through a real Kubernetes Gateway — there is no standalone-proxy path for
this guide, unlike other guides in this repo. Deploy one first if your cluster doesn't
already have one:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster.
2. *Deploy the llm-d Router*:

```bash
export PROVIDER_NAME=gke # other: na, agentgateway, or istio
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

### 2. Provision the shared model cache

The three model server pods and the Coordinator's render container share a single
`PersistentVolumeClaim` (`llm-d-model-cache`) for the HuggingFace model files, so the
model is downloaded once and reused. The claim is `ReadWriteMany` and 250Gi.

> [!IMPORTANT]
> Edit [`model-cache-pvc.yaml`](model-cache-pvc.yaml) to set `storageClassName` to an
> RWX-capable StorageClass available in your cluster (e.g. NFS, CephFS, EFS, GCP
> Filestore). If your cluster's default StorageClass is already RWX-capable, leave the
> field commented out.

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/model-cache-pvc.yaml
```

> [!NOTE]
> The first model server pod to start will populate the cache via the HuggingFace
> Hub; the others reuse it. Expect the first cold start to be longer than the rest.

### 3. Deploy the Model Servers

Apply the Kustomize overlay for your infrastructure provider. One overlay deploys all
three role-specific model servers (encode, prefill, decode), each as a single
replica:

```bash
export INFRA_PROVIDER=base # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
```

### 4. Deploy the Coordinator

Drives the `replace-media-urls → render → conditional-decode → encode → prefill →
decode` pipeline. The ConfigMap references `${NAMESPACE}` and `${PROVIDER_NAME}`, so
build with `kustomize` and pipe through `envsubst` before applying:

```bash
export PROVIDER_NAME=${PROVIDER_NAME:-istio} # match whatever provider you used for the routers, or your standalone proxy
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/ | envsubst | kubectl apply -n ${NAMESPACE} -f -
```

### 5. (Optional) Deploy the multimedia downloader (caching proxy)

The Coordinator's `replace-media-urls` step can route outbound media fetches through
an in-cluster forward proxy (e.g. Squid) that caches origin images/video, eliminating
redundant fetches across requests. Caching HTTPS origins requires the proxy to
terminate TLS and re-sign responses with its own CA (SSL-Bump), which means the
Coordinator needs to trust that CA.

This repo doesn't ship a caching proxy of its own — deploy one for your cluster (e.g.
an SSL-Bump-configured Squid), then trust its CA in the Coordinator with
[`multimedia-downloader/patch-coordinator-ca.yaml`](multimedia-downloader/patch-coordinator-ca.yaml).
This requires the Coordinator from step 4 to already be deployed:

```bash
kubectl patch deployment llm-d-coordinator -n ${NAMESPACE} \
    --type=strategic --patch-file ${REPO_ROOT}/guides/${GUIDE_NAME}/multimedia-downloader/patch-coordinator-ca.yaml
kubectl rollout restart deployment/llm-d-coordinator -n ${NAMESPACE}
```

Without this step, `replace-media-urls` still works — it just fetches media directly
instead of through a cache.

### 6. (Optional) Enable monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).

## Verification

### 1. Get the IP of the Entrypoint

Clients always send requests through the **Gateway**, never directly to the
Coordinator's Service. The Coordinator's own `coordinator` HTTPRoute (deployed
alongside the coordinator overlay in step 4) attaches to the `llm-d-inference-gateway`
Gateway and forwards client traffic (no `EPP-Profile` header) to the Coordinator, which
then orchestrates the `encode → prefill → decode` pipeline. The EPP is internal — the
Coordinator selects which of its scheduling profiles runs by setting the `EPP-Profile`
header on its own outbound calls, which route back through that same Gateway to
`router/httproute.yaml` (step 1.3) instead of the Coordinator's route.

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
export PORT=80
```

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
        "model": "Qwen/Qwen3-VL-2B-Instruct",
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
        "model": "Qwen/Qwen3-VL-2B-Instruct",
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

To remove the deployed components:

```bash
envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute.yaml | kubectl delete -n ${NAMESPACE} -f -
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/ | envsubst | kubectl delete -n ${NAMESPACE} -f -
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/model-cache-pvc.yaml
kubectl delete namespace ${NAMESPACE}
```

If nothing else in your cluster still uses it, also remove the `llm-d-inference-gateway` Gateway by following [the gateway cleanup guide](../../docs/infrastructure/gateway/gke.md#cleanup).

## Architecture

### Coordinator vs. Routing Sidecar

Standard [P/D Disaggregation](../pd-disaggregation/README.md) dispatches requests via
a **Routing Sidecar** co-located with the decode worker: the EPP picks a P/D pair, and
the sidecar runs a fixed protocol (`max_tokens=1, do_remote_decode=True` to prefill,
then hand the `KVTransferParams` to decode). See
[Disaggregated Serving: Request Flow Orchestration](../../docs/architecture/advanced/disaggregation/README.md#request-flow-orchestration)
for the full sequence.

The Coordinator generalizes that fixed protocol into a **pipeline of named steps**
declared in its `ConfigMap`. Each role (encode, prefill, decode) is still just an
independent vLLM replica, now behind a single shared EPP/InferencePool rather than one
per role — the same NIXL `kv-transfer-config` / `ec-transfer-config` connectors do the
actual KV and encoder-cache transfer. What changes is *where the dispatch logic
lives*: instead of being hardcoded in a sidecar binary running in every decode pod,
it's centralized in one Coordinator service and expressed as data (the `steps:` list
and the EPP's `schedulingProfiles`), which is what makes it possible to add, remove,
or reorder phases without shipping new code, and to pick each phase's pod only when
that phase is about to run instead of all of them up front (see
[Deferred decoding](#overview)).
