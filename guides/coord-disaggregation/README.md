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
  `ConfigMap` (see [`coordinator/base/configmap.yaml`](coordinator/base/configmap.yaml)). Steps
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
  plugin. Each profile filters the shared pool down to its own role with a role filter.
* **Or: 3 EPPs**, one per role, each with its own **InferencePool** scoped to that
  role's pods via `modelServers.matchLabels`. Each EPP runs a single `default`
  scheduling profile picked implicitly by the chart's `single-profile-handler`
  (no profile-selection plugin needed, since a role-scoped EPP never has to
  choose between profiles) — no custom image either. Each role's file does
  configure its own scorer plugins (see below); that's just picking which
  endpoint within the role, not which profile to run. The Gateway's `HTTPRoute`
  (not the EPP) is what dispatches each `EPP-Profile` value to the right EPP's
  InferencePool.

### When to use the Coordinator

Calling the EPP once per phase cuts both ways. The extra Gateway/EPP round trip is a
cost paid on every request, and the later decode pick it enables is a benefit only in
some regimes. The [Benchmark](#benchmark) section measures each in isolation against
the [P/D Disaggregation](../pd-disaggregation/README.md) sidecar; this is what the
results come down to.

**The overhead is a few milliseconds of TTFT, and nothing else.** Across every run
the extra per-phase hop shows up only in time to first token, as a single-digit
millisecond gap, and never in time per output token or end-to-end request latency.
Under concurrent load it disappears entirely:

| Setting | TTFT (Coordinator vs. sidecar) | E2E latency / ITL |
|---|---|---|
| Single request, text (`gpt-oss-120b`, 1–1,000 input or 100–2,500 output tokens) | median 1.6–4.9% higher | within ±1.5% / ±0.7% |
| Concurrent spikes, text (`DeepSeek-V2`, 50–500 concurrent) | 10–12% *lower* at ≤100 concurrent, tied above | tied, no consistent edge |
| Prefill-bound multimodal (`Qwen3-VL-235B`, 1P/3D) | 4–8% higher | tied |
| Decode-contended multimodal, closed loop (`Qwen3-VL-235B`, 2P/2D, 50–300 concurrent) | p99 within 1–4% | within ~2% |

**The benefit is real but conditional.** With heavy-tailed multimodal requests
released in bursts against KV-saturated decode pods, the Coordinator delivers 4–10%
higher throughput and 5–10% lower E2E latency at every burst size, and at the
heaviest burst cuts p99 TTFT by 15% and mean TPOT by 12%, because its later decode
pick keeps the decode pods balanced where the sidecar's up-front placement lets them
drift apart. Under uniform or steady load, or when prefill is the bottleneck, the two
architectures tie.

**Use the Coordinator when:**

* **You need the pipeline.** Encode / Prefill / Decode topologies, multimodal inputs
  with a separate encode stage, or any deployment where you want to add, drop, or
  reorder phases from a `ConfigMap`. The sidecar dispatches a fixed prefill → decode
  sequence only. This is the primary reason to pick it, and it is independent of the
  numbers above.
* **Decode is the contended stage, per-request load is heterogeneous, and arrivals
  are bursty.** All three together are what deferred decoding needs to pay off (see
  [When to expect an advantage from deferred decoding](#deferred-decoding-under-heavy-multimodal-load-qwen3-vl-235b-h200)).
  Expect single- to low-double-digit gains in throughput and tail latency, which more
  than cover the few milliseconds of TTFT the extra hop costs.
* **Your EPP scores decode on scraped vLLM metrics** (`queue-scorer`,
  `kv-cache-utilization-scorer`). Those signals lag and omit requests still in
  prefill, so the sidecar's early pick is working from staler data than in these
  benchmarks, and the Coordinator's gap will be larger than measured here.

**Stay with the [P/D Disaggregation](../pd-disaggregation/README.md) sidecar when:**

* **It is text-only P/D and prefill is the bottleneck, or load is uniform and
  steady.** The decode choice does not matter in that regime, so there is nothing for
  deferred decoding to improve, and the Coordinator is a small net cost (a few percent
  of TTFT).
* **Single-request TTFT is budgeted to the millisecond.** The overhead is small but
  consistent, and only shows up there.
* **You need the more mature path.** The Coordinator is experimental and less tested
  (see the warning in the [Overview](#overview)); the sidecar is the established
  architecture.

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
> Encoder-cache transfer between instances (the P2P NIXL mode of `--ec-transfer-config`,
> [vllm-project/vllm#47941](https://github.com/vllm-project/vllm/pull/47941)) and the
> `--enable-scale-out` flag
> ([vllm-project/vllm#55176](https://github.com/vllm-project/vllm/pull/55176)) require
> vLLM `v0.30.0` or later, so the model server manifests use the upstream vLLM `v0.30.0`
> image (`docker.io/vllm/vllm-openai:v0.30.0`), the same one the E/PD and E/P/D profiles
> of the [Encode Disaggregation guide](../multimodal-serving/e-disaggregation/README.md)
> use. The encode and prefill model servers set `VLLM_USE_V2_MODEL_RUNNER=1`,
> which the
> [CPU EC connector](https://docs.vllm.ai/en/v0.30.0/features/ec_cpu_connector/)
> requires, and use its P2P NIXL mode (`ec_enable_nixl`, `ec_cpu_bytes`).
>
> vLLM `v0.29.0` and earlier releases accept `"ec_enable_nixl": true` but do not read it:
> the pods start and requests succeed, but no encoder output is transferred and the
> prefill model server encodes the media again. No error is reported. To confirm that the
> transfer occurs, follow
> [Confirm the EC Transfer](../multimodal-serving/e-disaggregation/README.md#3-confirm-the-ec-transfer-vllm-profiles)
> with `EC_CONSUMER_ROLE=prefill`.

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
  # GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
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
>   behind it is simply never called, since the Coordinator's pipeline (step 3) is what
>   decides whether an `encode` phase call happens at all.
> * Step 2: after applying the overlay, scale the encode Deployment to 0 replicas:
>
>   ```bash
>   kubectl scale deployment/coord-disaggregation-nvidia-gpu-vllm-encode -n ${NAMESPACE} --replicas=0
>   ```
>
> * Step 3: after deploying the Coordinator (either overlay — this patch is
>   provider-independent), apply
>   [`coordinator/base/patch-pd-only.yaml`](coordinator/base/patch-pd-only.yaml) to drop
>   the `replace-media-urls`, `render`, and `encode` steps from `pipeline.steps` (keeping
>   only `conditional-decode`, `prefill`, and `decode`), then restart the coordinator
>   Deployment:
>
>   ```bash
>   kubectl patch configmap llm-d-coordinator-config -n ${NAMESPACE} --type=strategic \
>       --patch="$(envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/base/patch-pd-only.yaml)"
>   kubectl rollout restart deployment/llm-d-coordinator -n ${NAMESPACE}
>   ```
>
> * Step 4: skip entirely — the multimedia downloader is only used by the `replace-media-urls` pipeline step.

### 1. Deploy the llm-d Router

Pick **one** of the two topologies from the [Overview](#overview) — single EPP
(default below) or 3 separate EPPs (in the collapsed section further down). Don't do
both; they install into the same namespace and would conflict.

Both topologies default to routing the Coordinator's own ingress
(`coordinator/base/httproute.yaml`) and its outbound `gateway.address`
([`coordinator/base/configmap.yaml`](coordinator/base/configmap.yaml)) through a real
Kubernetes Gateway. Deploy one first if your cluster doesn't already have one:

> [!NOTE]
> The llm-d Router's Standalone Mode (no Kubernetes Gateway, just the router's own
> Envoy sidecar and Service) is **not supported by this guide** — only the Gateway
> Mode wiring is documented and maintained.
> Use Gateway Mode for both EPP topologies.

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster.

#### Single EPP (default)

One llm-d Router release, EPP, and InferencePool cover all three roles.

1. *Deploy the llm-d Router*:

```bash
export PROVIDER_NAME=istio # other: gke, na, agentgateway
export ROUTER_RELEASES=${GUIDE_NAME} # Helm release name(s), for Cleanup
export ROUTER_HTTPROUTE_OVERLAY=router/httproute/base # router/httproute/gke for PROVIDER_NAME=gke; used below and for Cleanup
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

1. *Deploy the Router's HTTPRoute*. The chart's own auto-created HTTPRoute is disabled
   (`httpRoute.create: false` in [`router/coord-disaggregation.values.yaml`](router/coord-disaggregation.values.yaml))
   because it would be an unconditional catch-all on `/`, colliding with the
   Coordinator's own route on the same Gateway. Instead, the two hand-authored
   HTTPRoutes on this Gateway (`coordinator/base/httproute.yaml` and
   [`router/httproute/base/httproute.yaml`](router/httproute/base/httproute.yaml))
   split traffic three ways:
   * `/v1/completions`, `/v1/chat/completions`, `/inference/v1/generate` **without**
     `EPP-Profile` → the Coordinator (client-facing inference calls).
   * The same three paths **with** `EPP-Profile` → this router's EPP (the Coordinator's
     own internal per-phase calls reuse those same paths, so both HTTPRoutes match
     them at the same exact-path specificity; the header match then breaks the tie in
     the router's favor — see the comments in both files for why path specificity
     must match for this to work).
   * Everything else without `EPP-Profile` (e.g. `/v1/models`, `/health`) → this
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
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/${ROUTER_HTTPROUTE_OVERLAY}/ | envsubst | kubectl apply -n ${NAMESPACE} -f -
```

> [!NOTE]
> The two overlays differ the same way as the Coordinator's (step 3):
>
> * `base` — the plain HTTPRoute, with a 300s `timeouts.request`.
> * `gke` — drops the HTTPRoute's `timeouts` field (GKE Gateway does not implement
>   it) and instead adds a
>   [`GCPBackendPolicy`](router/httproute/gke/gcpbackendpolicy.yaml) on the shared
>   InferencePool carrying the same 300s timeout, which is where GKE takes it from.

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

1. *Deploy the llm-d Routers*:

```bash
export PROVIDER_NAME=istio # other: gke, na, agentgateway
export ROUTER_RELEASES="${GUIDE_NAME}-encode ${GUIDE_NAME}-prefill ${GUIDE_NAME}-decode" # for Cleanup
export ROUTER_HTTPROUTE_OVERLAY=router/httproute-3-epp/base # router/httproute-3-epp/gke for PROVIDER_NAME=gke; used below and for Cleanup
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

1. *Deploy the shared HTTPRoute*. Same reasoning as the single-EPP variant's
   `httpRoute.create: false` (each release disables its own auto-created HTTPRoute for
   the same specificity-tie reason — see the comment in
   [`router/httproute-3-epp/base/httproute.yaml`](router/httproute-3-epp/base/httproute.yaml)),
   but instead of one shared backend, that HTTPRoute routes each `EPP-Profile` value
   to its own role's InferencePool (`${GUIDE_NAME}-encode`, `${GUIDE_NAME}-prefill`,
   `${GUIDE_NAME}-decode` — the InferencePool name matches the Helm release name). The
   same [!WARNING] about `EPP-Profile` not being a trust boundary applies here too.

```bash
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/${ROUTER_HTTPROUTE_OVERLAY}/ | envsubst | kubectl apply -n ${NAMESPACE} -f -
```

> [!NOTE]
> Same `base`/`gke` overlay split as the single-EPP variant's HTTPRoute, except the
> `gke` overlay adds one
> [`GCPBackendPolicy`](router/httproute-3-epp/gke/gcpbackendpolicy.yaml) per role
> InferencePool (three in total) rather than a single shared one.

</details>

### 2. Deploy the Model Servers

Apply the Kustomize overlay for your infrastructure provider. One overlay deploys all
three role-specific model servers (encode, prefill, decode), each as a single
replica:

```bash
export INFRA_PROVIDER=base # base | coreweave | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
```

Then deploy the render Service. The Coordinator's `render` step (step 3) sends its
requests to this Service. The Service owns no pods: it fronts the prefill model
servers, which serve vLLM's `/v1/*/render` endpoints, so render capacity grows with
the prefill replicas. See [`render/service.yaml`](render/service.yaml) for why it
selects the prefill pods.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```

> [!NOTE]
> Each model server pod pulls
> its own copy of the model from the HuggingFace Hub independently — there's no shared
> model cache between them, matching the default convention used by other guides in
> this repo (e.g. [P/D Disaggregation](../pd-disaggregation/README.md)). Expect the
> first cold start to take a while on every pod, not just one; if that's a problem in
> your cluster (slow/metered egress, many replicas), add an RWX-backed
> `PersistentVolumeClaim` mounted at a shared `HF_HOME` path across these manifests
> instead.

### 3. Deploy the Coordinator

Drives the `replace-media-urls → render → conditional-decode → encode → prefill →
decode` pipeline.

First point the Coordinator at the Gateway. Every per-phase call it makes goes back
out through that same Gateway, so `${GATEWAY_ADDRESS}` — referenced by
[`coordinator/base/configmap.yaml`](coordinator/base/configmap.yaml) — has to be an
address reachable from *inside* the cluster, and how you get one differs per
provider. This is set here rather than in step 1 because on GKE the address doesn't
exist until the Gateway has been reconciled (wait for `PROGRAMMED=True`, see
[the GKE gateway guide](../../docs/infrastructure/gateway/gke.md#step-3-verify-the-gateway)):

```bash
case "${PROVIDER_NAME}" in
  # GKE's Gateway is a Google Cloud load balancer -- there is no in-cluster
  # Service to target, so use the address the controller published on the
  # Gateway's status (reachable from in-cluster for both GKE gateway classes).
  gke)
    export COORDINATOR_OVERLAY=gke
    export GATEWAY_ADDRESS="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}'):80"
    ;;
  # agentgateway's generated Service takes the Gateway's own name, with no
  # -<provider> suffix (unlike istio/na) -- https://agentgateway.dev/docs/kubernetes/latest/setup/gateway/
  agentgateway)
    export COORDINATOR_OVERLAY=base
    export GATEWAY_ADDRESS="http://llm-d-inference-gateway.${NAMESPACE}.svc:80"
    ;;
  *)
    export COORDINATOR_OVERLAY=base
    export GATEWAY_ADDRESS="http://llm-d-inference-gateway-${PROVIDER_NAME}.${NAMESPACE}.svc:80"
    ;;
esac
echo "GATEWAY_ADDRESS=${GATEWAY_ADDRESS}" # must be non-empty
```

Then build the overlay for your Gateway provider with `kustomize` and pipe it
through `envsubst` before applying:

```bash
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/${COORDINATOR_OVERLAY}/ | envsubst | kubectl apply -n ${NAMESPACE} -f -
```

> [!NOTE]
> The two overlays differ only in what the Gateway provider needs on the
> Coordinator's own Service and HTTPRoute:
>
> * `base` — the plain manifests, with a 300s `timeouts.request` on
>   [`coordinator/base/httproute.yaml`](coordinator/base/httproute.yaml).
> * `gke` — adds a [`HealthCheckPolicy`](coordinator/gke/healthcheckpolicy.yaml)
>   (GKE Gateway does not derive health checks from the pod's readiness/liveness
>   probes; without it the load balancer probes `/`, gets a 404, marks the
>   Coordinator unhealthy, and every request returns 503) and a
>   [`GCPBackendPolicy`](coordinator/gke/gcpbackendpolicy.yaml) carrying the 300s
>   timeout, which GKE takes there instead of from the HTTPRoute's unsupported
>   `timeouts` field (dropped by the overlay).

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

## Benchmark

The benchmarks in this section answer two separate questions about the Coordinator.
[When to use the Coordinator](#when-to-use-the-coordinator) in the Overview condenses
the results into guidance; the sections below hold the full setups and numbers.

1. **What does the Coordinator cost?** Every pipeline phase is its own round trip
   through the Gateway and EPP, where the sidecar's decode pod calls prefill directly
   in one hop. [Overhead of the extra per-phase hop](#overhead-of-the-extra-per-phase-hop)
   measures that cost in isolation: both arms were forced down the same
   prefill → decode path on every request, so the only difference left was the number
   of hops.
2. **What does the Coordinator buy?** It picks the decode pod only after prefill has
   finished, on the pool's current state, where the sidecar commits prefill and decode
   together before prefill starts.
   [Deferred decoding under heavy multimodal load](#deferred-decoding-under-heavy-multimodal-load-qwen3-vl-235b-h200)
   measures that benefit under a workload chosen to make the later pick matter.

### Overhead of the extra per-phase hop

The Coordinator adds a real architectural cost per request: every pipeline phase
(`encode`, `prefill`, `decode`) is its own round trip through the Gateway and EPP
(`conditional-decode` → `encode` → `prefill` → `decode`, each a separate `ext_proc`
scheduling decision), versus the [P/D Disaggregation](../pd-disaggregation/README.md)
sidecar's decode pod calling prefill directly in one hop. That's close to double the
local network hops for a P/D-only request.

To isolate that hop-count difference instead of comparing two different decisions about
*whether* to disaggregate a given request, both sides of the benchmark were configured
to always take the full prefill → decode path: the sidecar ran with the routing
sidecar's `always-disagg-pd-decider` plugin (always
dispatching a separate prefill call), and the Coordinator ran the
[PD-only pipeline](#installation-instructions) (`prefill` → `decode`, no
`conditional-decode` step, so it never takes the optimistic decode-first fast path
described in the [Overview](#overview)). With both sides guaranteed to hit prefill on
every request, the only architectural difference left is the Coordinator's extra
Gateway/EPP round trip per phase, and that extra hop count barely shows up in TTFT
(time to first token) or TTOT/ITL (time per output token), and under concurrent load
it doesn't show up at all: the Coordinator matches the sidecar's latency, and at
several concurrency levels beats it.

Both single-request sweeps below share the same deployment: `openai/gpt-oss-120b` on
H200 GPUs, decode at 1 replica × TP4 (4 GPUs), prefill at 1 replica × TP1 (1 GPU), 5
GPUs total, identical on both architectures.

* **[Varying input prompt length](https://github.com/dmitripikus/coordinator-performance/tree/main/pd-comparison-analysis/inconcurrent_var_prompt_always_disaggr_pinned)**
  (1, 10, 100, 1,000 prompt tokens; fixed 20-token output; prefill/decode pinned to
  identical nodes on both architectures to isolate architecture from node variance):

  <p float="left">
    <img src="benchmark-results/inconcurrent_var_prompt_always_disaggr_pinned/ttft_distribution.png" width="45%" />
    <img src="benchmark-results/inconcurrent_var_prompt_always_disaggr_pinned/request_latency_distribution.png" width="45%" />
  </p>

  Median TTFT is 1.8-4.8% higher with the Coordinator across all four prompt lengths
  (e.g. 40.36ms vs. 38.51ms at 10 tokens); median request latency is within 0.2-1.5%;
  median ITL (time per output token) is within ±0.7%, indistinguishable from
  measurement noise. An ITL p90-p10 spread roughly 2-3x wider with the Coordinator
  (~2.4-3.2ms vs. sidecar's ~0.8-1.5ms) is the one open secondary finding; it doesn't
  move the median, and isn't root-caused in the linked analysis.

* **[Varying output length](https://github.com/dmitripikus/coordinator-performance/tree/main/pd-comparison-analysis/inconcurrent_var_output_always_disaggr_pinned)**
  (100, 500, 1,000, 2,500 output tokens; fixed 250-token input; same node-pinning):

  <p float="left">
    <img src="benchmark-results/inconcurrent_var_output_always_disaggr_pinned/ttft_distribution.png" width="45%" />
    <img src="benchmark-results/inconcurrent_var_output_always_disaggr_pinned/request_latency_distribution.png" width="45%" />
  </p>

  Median TTFT is 1.6-4.9% higher with the Coordinator; median request latency is
  within ±0.9% and median ITL within ±0.35% across all four output lengths: the two
  architectures are described as "essentially identical" here, with the Coordinator
  showing a heavier decode-tail (occasional slow tokens raising p95/p99 ITL) that
  doesn't move the median.

* **[Concurrent stress spikes](https://github.com/dmitripikus/coordinator-performance/blob/main/pd-comparison-analysis/6Dx4GPU_4Px8GPU_DeepSeek-V2_concurrent/coord_vs_sidecar_random_spikes_summary.md)**:
  a larger, concurrent deployment to check whether the extra hop shows up under
  load instead of one request at a time. `deepseek-ai/DeepSeek-V2` on H200 GPUs,
  decode at 6 replicas × TP4 (24 GPUs), prefill at 4 replicas × TP8 (32 GPUs), 56
  GPUs total, identical on both architectures. `inference-perf`'s `random_spikes`
  scenario drove fixed 1000/1500-token input/output requests through 9 concurrency
  stages from 50 to 500; every stage hit 100% success on both sides.

  <p float="left">
    <img src="benchmark-results/6Dx4GPU_4Px8GPU_DeepSeek-V2_concurrent/ttft_distribution.png" width="45%" />
    <img src="benchmark-results/6Dx4GPU_4Px8GPU_DeepSeek-V2_concurrent/request_latency_distribution.png" width="45%" />
  </p>

  At the two lowest concurrency stages the Coordinator is ahead on both metrics that
  matter most for latency-sensitive traffic: median request latency is about 10%
  lower at 50 concurrent requests and 7% lower at 100, and median TTFT is about 12%
  lower at 50 and 10% lower at 100. From 150 concurrent requests up, both metrics
  converge to within about 2-7% either way, with neither side holding a consistent
  edge (ITL differences stay small throughout, roughly 0.1-1.9ms at any given
  stage). Both scale similarly as load rises (TTFT from ~350-390ms to ~1850-1920ms,
  ITL from ~16.6-18.5ms to ~37-37.2ms across the range).

**Bottom line**: the Coordinator's extra per-phase network hop is measurable in TTFT
(a consistent few-percent, single-digit-millisecond gap) but not in ITL/TTOT or overall
request latency, which track the sidecar architecture within about 1.5% across every
prompt and output length tested. Under concurrent load the extra hop doesn't cost
anything either: at low concurrency (up to ~100 concurrent requests) the Coordinator
actually has *lower* request latency and TTFT than the sidecar, and from there on up
the two are essentially tied. More network hops, in other words, don't translate
into a performance penalty.

### Deferred decoding under heavy multimodal load (Qwen3-VL-235B, H200)

The [overhead runs above](#overhead-of-the-extra-per-phase-hop) forced both arms
down the same prefill → decode path and found the Coordinator's extra Gateway/EPP round
trip per phase costs a few milliseconds, at most a few percent of TTFT.

This benchmark asks the other question: *is there a benefit to picking the decode pod
later?* The sidecar picks prefill and decode together, before prefill starts; the
Coordinator picks decode only after prefill has finished, when the pool's state may
have changed. It was run under a workload chosen to make that difference matter —
long multimodal prefills, long outputs, and decode pushed to its KV-cache limit.

This benchmark runs with 4 × `a3-ultragpu-8g` (8 × H200 141GB each),`Qwen/Qwen3-VL-235B-A22B-Instruct` on vLLM v0.26.0, TP=8 (one pod per node),
NixlConnector over GKE multi-network RDMA, 2 prefill + 2 decode pods. Both arms run in
Gateway mode behind the same GKE Gateway, with the same EPP image and this guide's
scheduling profiles on both sides: prefill = `prefix-cache-affinity-filter` +
`token-load-scorer`, decode = `active-request-scorer`. The sidecar arm is the
[P/D Disaggregation](../pd-disaggregation/README.md) guide (`always-disagg-pd-decider`);
the Coordinator arm runs the PD-only pipeline `prefill → decode` (no `conditional-decode`,
so every request does prefill then decode on both arms). Load comes from
[inference-perf](https://github.com/kubernetes-sigs/inference-perf) running in-cluster:
~300 text tokens plus 1–14 synthetic images per request, every request unique, same
seed on both arms, each load level run twice.

**Where the two architectures tie.** With a fixed 6000-token output and 1–12 × 1080p
images per request, a closed-loop concurrency ladder (50 → 300) is indistinguishable
between the arms: throughput, TPOT, ITL and E2E latency agree within ~2% at every
level and TTFT p99 within 1–4%. Bursts of 160–280 requests with the same per-request
shape give the Coordinator a ~10% mean/p99 TTFT edge only right at the decode
KV-capacity knee (N≈160) and nothing above it, where both decode pods queue regardless
of routing. A 1 prefill + 3 decode layout is prefill-bound: the Coordinator keeps the
three decode pods visibly more even (running-request gap 2 vs 6) but decode is never
contended, so nothing improves and the extra per-phase Gateway hop costs 4–8% of TTFT.

**Where the Coordinator wins.** Keep 2P/2D, make the per-request load heavy-tailed —
output length log-normal (mean 2500, std 3000, capped at 11000 tokens) and images
1–14 × 360p/1080p mixed, i.e. ~10× spread in both KV footprint and decode residency —
and release the requests in bursts of 300 / 360 / 420 so that the two decode pods run
at 125–145 concurrent requests and 85–100% KV. Means of two repeats per level
(sidecar / Coordinator):

| Burst size | Throughput (req/s) | Mean E2E (s) | Mean TTFT (s) | P99 TTFT (s) | Mean TPOT (ms) |
|---|---|---|---|---|---|
| 300 | 0.93 / **1.03** | 137 / **124** | 45.9 / **41.8** | 92 / **90** | 44.0 / 44.6 |
| 360 | 1.01 / **1.06** | 150 / **143** | 55.0 / **52.6** | 115 / **106** | 46.1 / 46.6 |
| 420 | 1.05 / **1.15** | 171 / **154** | 64.1 / **61.1** | 147 / **124** | 52.1 / **45.7** |

<p float="left">
  <img src="benchmark-results/2Px8GPU_2Dx8GPU_Qwen3-VL-235B-A22B_burst/2p2d_throughput.png" width="45%" />
  <img src="benchmark-results/2Px8GPU_2Dx8GPU_Qwen3-VL-235B-A22B_burst/2p2d_latency.png" width="45%" />
</p>
<p float="left">
  <img src="benchmark-results/2Px8GPU_2Dx8GPU_Qwen3-VL-235B-A22B_burst/2p2d_decode_imbalance.png" width="60%" />
</p>

Throughput is 4–10% higher and E2E latency 5–10% lower at every burst size, and at 420
requests — the one level where the sidecar's decode pods drift far enough apart to
exhaust one pod's KV (per-pod running-request gap up to 71, 84 requests queued behind
the fuller pod while the other drained) — p99 TTFT drops 15% and mean TPOT 12%.
The per-pod traces show the mechanism directly: under the sidecar the two decode
pods start level and separate as short requests finish, because every placement was
fixed at t=0; under the Coordinator they stay within ~15 requests of each other for
the whole burst.

**When to expect an advantage from deferred decoding.** The EPP picks the decode pod
with the fewest in-flight requests, and the in-flight count is maintained in the
EPP process at *scheduling* time, not read back from vLLM. So the sidecar's early
pick is not working from stale data — it is working from an exact reservation made
before prefill starts — and the Coordinator's later pick only adds information when
all three of the following hold:

1. **Decode is the contended stage** (KV near its limit or per-step latency rising with
   batch size). If prefill is the bottleneck the decode choice does not matter and the
   Coordinator's extra round trip per phase is a small net cost.
2. **Per-request load is heterogeneous** — long-tailed output lengths and/or widely
   varying prompt (image) sizes — so that an equal *count* of requests per pod is not
   an equal *load*. With uniform requests the count is already the right measure.
3. **Placement is committed in bulk** (bursts). Deciding at prefill completion lets
   the Coordinator see which pod has actually freed KV since the burst arrived; in a
   steady stream the sidecar's counts receive the same feedback from completions and
   the two converge.

Deployments that pick decode from scraped vLLM metrics (`queue-scorer`,
`kv-cache-utilization-scorer`) will see a larger gap than measured here, because those
signals lag and do not include requests still in prefill; the in-process `token-load-scorer`
`active-request-scorer` used by both guides removes most of that staleness.

## Cleanup

Same commands regardless of topology — `${ROUTER_RELEASES}` and
`${ROUTER_HTTPROUTE_OVERLAY}` were exported in step 1, `${COORDINATOR_OVERLAY}`
in step 3. The router HTTPRoute and the Coordinator are torn down by rebuilding
the same overlay that created them, so provider-specific resources (the GKE
`HealthCheckPolicy` and `GCPBackendPolicy`s) go with them; `--ignore-not-found`
keeps that safe if you never got as far as applying some of them:

```bash
for RELEASE in $(echo ${ROUTER_RELEASES}); do
  helm uninstall ${RELEASE} -n ${NAMESPACE}
done
kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/${ROUTER_HTTPROUTE_OVERLAY}/ | envsubst | kubectl delete -n ${NAMESPACE} --ignore-not-found -f -

kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/${COORDINATOR_OVERLAY}/ | envsubst | kubectl delete -n ${NAMESPACE} --ignore-not-found -f -

kubectl delete -n ${NAMESPACE} --ignore-not-found -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

This deletes every resource the guide created, but leaves the namespace itself (and
anything else in it, like the `llm-d-hf-token` secret) alone — `kubectl delete
namespace ${NAMESPACE}` if you want it gone entirely.

If nothing else in your cluster still uses it, also remove the
`llm-d-inference-gateway` Gateway by following [the gateway istio cleanup guide](../../docs/infrastructure/gateway/istio.md#cleanup), or
[the agentgateway cleanup guide](../../docs/infrastructure/gateway/agentgateway.md#cleanup).
