# Serve the Responses API with Agentic API (Experimental)

Add the OpenAI-compatible **Responses API** — stateful multi-turn conversations, webhook tool
loops and WebSocket streaming — to a deployment you already have, by putting
[vLLM Agentic API](https://github.com/vllm-project/agentic-api/blob/main/docs/deploying/README.md)
and a PostgreSQL state store in front of its llm-d Router.

## Overview

This is an **extension**, not a standalone deployment. It assumes some other llm-d guide is
already deployed and serving, and it adds exactly three things to that namespace:

| Added | What it is |
| --- | --- |
| `Deployment/agentic-api` + `Service/agentic-api:9000` | `vllm/agentic-api`, configured to use the base guide's router as its inference backend |
| `Deployment/agentic-api-postgres` + PVC | PostgreSQL 17, which is what makes `previous_response_id` continuation work across requests |
| 2 × `HTTPRoute` (Gateway Mode only) | Put `agentic-api` in front of the base guide's `InferencePool` for the agentic paths |

It does **not** touch the base guide. No `helm upgrade`, no `httpRoute.create=false`, no EPP
restart — the base guide stays installed exactly as its own README describes, and removing this
extension leaves it as it was.

### Which guides can it extend?

Any guide that deploys the llm-d Router **with vLLM as its model server**. The engine is not
interchangeable here: `agentic-api` owns conversation state and delegates each inference round to
*vLLM's own stateless Responses API*, so the upstream must expose `/v1/responses`
([`ARCHITECTURE.md`](https://github.com/vllm-project/agentic-api/blob/main/ARCHITECTURE.md), ADR-01
§1.1). SGLang and TensorRT-LLM overlays are therefore out of scope, as are the tool-calling flags
below, which are vLLM CLI flags.

Beyond the engine, the coupling is just a name. The router chart derives every resource name from
its helm release name, which is the base guide's `GUIDE_NAME`:

```
helm release ${BASE_GUIDE_NAME}  ->  Service/${BASE_GUIDE_NAME}-epp
                                     InferencePool/${BASE_GUIDE_NAME}
                                     HTTPRoute/${BASE_GUIDE_NAME}       (Gateway Mode)
```

so `BASE_GUIDE_NAME` is all this guide needs in order to find them. The examples below use
[`pd-disaggregation`](../pd-disaggregation/README.md); substitute
[`optimized-baseline`](../optimized-baseline/README.md),
[`wide-ep`](../wide-ep/README.md) or any other guide that deploys the router with a vLLM model
server. Within a guide that offers several engines, pick its vLLM overlay, for example
`modelserver/gpu/vllm/...` rather than `modelserver/gpu/sglang/...`.

- - -

## Prerequisites

### A deployed base guide, with tool calling enabled

Complete a guide such as [`pd-disaggregation`](../pd-disaggregation/README.md) first, in either
Standalone or Gateway Mode, and note its `GUIDE_NAME` and `NAMESPACE`.

#### Enable tool calling *before* deploying it

`agentic-api` orchestrates tool calls and reasoning output, so the **base guide's** vLLM must be
started with `--enable-auto-tool-choice` plus the parsers for its model. These are vLLM CLI flags
with no environment-variable equivalent, so they belong in the base guide's own model server
overlay — add them there **before** you deploy it, or you will have to roll the model servers a
second time. The parsers are per-model, and not every overlay sets them:

| Model | Flags | Already set by |
| --- | --- | --- |
| `openai/gpt-oss-120b` | `--enable-auto-tool-choice`<br>`--tool-call-parser=openai`<br>`--reasoning-parser=openai_gptoss` | [`pd-disaggregation`](../pd-disaggregation/modelserver/gpu/vllm/base/disaggregatedset.yaml), [`optimized-baseline/.../gpt-oss`](../optimized-baseline/modelserver/gpu/vllm/gpt-oss/patch-vllm.yaml), [`tiered-prefix-cache`](../tiered-prefix-cache/modelserver/gpu/vllm/base/patch-vllm-gpt-oss-120b.yaml) |
| `nvidia/Nemotron-3-Ultra` | `--enable-auto-tool-choice`<br>`--tool-call-parser=qwen3_coder`<br>`--reasoning-parser=nemotron_v3` | [`nemotron-3-ultra/modelserver/gpu/vllm`](../models/nemotron-3-ultra/modelserver/gpu/vllm/gke/patch-prefill.yaml) |

> [!IMPORTANT]
> Most model manifests in the repo still omit these flags — see
> [llm-d#2640](https://github.com/llm-d/llm-d/issues/2640) for the remaining models and why a
> reusable kustomize component was not the answer. Without them verification test `[3/4]` fails —
> it reports the missing flags by name rather than a bare assertion — and the pre-flight check at the
> end of this section reports it before you deploy anything.

For `pd-disaggregation` + `gpt-oss-120b`, the flags are added by
[llm-d#2641](https://github.com/llm-d/llm-d/pull/2641). Until that merges, or for any other
model, use one of the two workarounds below.

**Workaround A — edit the overlay before deploying the base guide (preferred).** This survives
re-applying the overlay, which is what the base guide's own instructions tell you to do. Add the
three flags to the model manifest, next to the other `vllm serve` args:

```bash
# e.g. guides/pd-disaggregation/modelserver/gpu/vllm/base/disaggregatedset.yaml (both roles)
#            - "--block-size=128"
#   +        - "--enable-auto-tool-choice"
#   +        - "--tool-call-parser=openai"
#   +        - "--reasoning-parser=openai_gptoss"
#
# Or take llm-d#2641 directly:
git fetch https://github.com/roytman/llm-d.git feat/pd-gpt-oss-tool-calling
git cherry-pick FETCH_HEAD
```

**Workaround B — patch an already-running deployment.** Faster if the base guide is already up,
but `kubectl apply -k` of the base overlay reverts it, and it only covers `Deployment`-based
topologies (not the `DisaggregatedSet` manifests of `pd-disaggregation` on NVIDIA GPU or `wide-ep`, which
need Workaround A).
Rolls the model servers, so weights reload:

```bash
# Run this after the base guide is deployed, in the same shell. That guide exports
# NAMESPACE, which is reused here as-is -- deliberately not overwritten, since yours may
# differ from its default. It exports the helm release name as GUIDE_NAME, which this
# guide calls BASE_GUIDE_NAME; left unset, the selector below reads
# `llm-d.ai/guide=`, matches nothing, and the loop patches nothing silently.
export BASE_GUIDE_NAME="${BASE_GUIDE_NAME:-${GUIDE_NAME:-}}"
: "${NAMESPACE:?NAMESPACE is unset: run this in the shell where you deployed the base guide, or set it to that namespace}"
: "${BASE_GUIDE_NAME:?BASE_GUIDE_NAME is unset: set it to the base guide helm release name, e.g. pd-disaggregation}"

# Selects only the vLLM model servers: the EPP Deployment carries none of these labels.
# `modelserver` is containers[0]; a decode pod's routing-proxy is an initContainer sidecar.
targets=$(kubectl get deploy -n "${NAMESPACE}" \
            -l llm-d.ai/engine-type=vllm,llm-d.ai/guide="${BASE_GUIDE_NAME}" -o name)
if [ -z "${targets}" ]; then
  echo "No vLLM model server Deployments matched in ${NAMESPACE} for guide ${BASE_GUIDE_NAME}." >&2
  echo "Check NAMESPACE/BASE_GUIDE_NAME, or use Workaround A if the guide uses a DisaggregatedSet." >&2
  exit 1
fi
echo "Patching:"; echo "${targets}"

# Piped into `while read` rather than `for d in ${targets}`: zsh does not word-split
# unquoted expansions by default, so a `for` loop would pass both Deployments to
# kubectl as one argument ("resource/name form may not have more than one slash").
printf '%s\n' "${targets}" | while read -r d; do
  [ -n "$d" ] || continue
  kubectl patch -n "${NAMESPACE}" "$d" --type=json -p '[
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--enable-auto-tool-choice"},
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--tool-call-parser=openai"},
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--reasoning-parser=openai_gptoss"}]'
done

kubectl rollout status -n "${NAMESPACE}" deploy -l llm-d.ai/engine-type=vllm --timeout=600s
```

> [!TIP]
> Re-running this appends the flags a second time. vLLM takes the last occurrence of a repeated
> argument, so it still starts, but check with
> `kubectl get deploy -n "${NAMESPACE}" -l llm-d.ai/engine-type=vllm -o json | grep -c enable-auto-tool-choice`
> and use Workaround A if you want the args to stay clean.

> [!WARNING]
> Both workarounds use the `gpt-oss` parser names. Substitute the parsers for **your** model from
> the table above; a wrong parser is worse than none, because vLLM then tries to parse tool calls
> with the wrong grammar.

#### Gateway Mode: the Gateway comes from the base guide

Nothing extra to do: a base guide deployed in Gateway Mode has already created the
`llm-d-inference-gateway` Gateway, because its own instructions say to (see
Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router),
which [`pd-disaggregation`](../pd-disaggregation/README.md) links to and which points at
[the gateway guides](../../docs/infrastructure/gateway) and
[`guides/recipes/gateway/<provider>`](../recipes/gateway)). The `Gateway` is namespaced, so one
exists per namespace and every llm-d guide there shares it, each contributing its own `HTTPRoute`.
This extension adds two more and touches neither the Gateway nor the base guide's route.

What does matter is that `PROVIDER_NAME` here matches the provider the base guide was installed
with. As [`guides/optimized-baseline`](../optimized-baseline/README.md) warns, the default `none`
renders no provider-specific resources: on GKE the `InferencePool` gets no `HealthCheckPolicy`, so
the Gateway marks the backend unhealthy and inference fails with 503s; on Istio the
`DestinationRule` the gateway needs to reach the EPP ext_proc endpoint over TLS is missing. Confirm
with `helm get values <base-guide-release> -n <namespace> | grep -A1 provider`.

- - -

## Architecture

### Standalone Mode

The base guide's EPP runs with an embedded Envoy sidecar, so `Service/${BASE_GUIDE_NAME}-epp`
speaks plain HTTP on `:80` and `agentic-api` dials it directly by DNS. No Gateway, no
`HTTPRoute`, nothing provider-specific — start here.

```mermaid
flowchart LR
    C["Client / verify.py<br>(HTTP /v1/responses, webhooks, WS)"]
    A["Service: agentic-api:9000"]
    PG[("PostgreSQL 17<br>agentic-api-postgres:5432")]
    R["Service: ${BASE_GUIDE_NAME}-epp:80<br>(standalone Envoy + EPP)"]
    V["Model servers<br>(base guide's topology)"]

    C -->|"/v1/responses"| A
    A <-->|"rehydrate & persist"| PG
    A -->|"--llm-api-base"| R
    R -->|"prefix & load-aware selection"| V
```

### Gateway Mode

Both `agentic-api` and the base guide's `InferencePool` sit behind the same
`llm-d-inference-gateway`. In Gateway Mode the EPP has no HTTP listener — only ext_proc on
`:9002` — so `agentic-api`'s own upstream call must go back out through the Gateway. The
`Host` header is what stops that from looping:

```mermaid
flowchart TB
    C["Client / verify.py"]
    GW["Gateway: llm-d-inference-gateway"]
    A["Service: agentic-api:9000<br>--llm-api-base http://epp.gateway.internal"]
    PG[("PostgreSQL 17")]
    POOL["InferencePool: ${BASE_GUIDE_NAME}<br>(EPP ext_proc :9002)"]
    V["Model servers"]

    C -->|"1. POST /v1/responses"| GW
    GW -->|"2. HTTPRoute/agentic-api<br>(path prefix wins)"| A
    A <-->|"3. state hydration"| PG
    A -->|"4. POST /v1/responses<br>Host: epp.gateway.internal"| GW
    GW -->|"5. HTTPRoute/agentic-api-internal<br>(hostname wins)"| POOL
    POOL -->|"6. prefix & load-aware selection"| V
```

`agentic-api` maps `epp.gateway.internal` to the Gateway address via Kubernetes `hostAliases` and
is given `--llm-api-base http://epp.gateway.internal`, so every upstream request it makes carries
`Host: epp.gateway.internal`. That leaves the `Authorization` header entirely free for end-user
OIDC/Bearer tokens.

### What the two HTTPRoutes do to routing

Both routes in [`manifests/gateway/routes.yaml`](manifests/gateway/routes.yaml) are **additive**.
The chart's `HTTPRoute/${BASE_GUIDE_NAME}` (a single `PathPrefix: /` rule) is left in place, and
these win over it on Gateway API matching precedence:

| Request | Matched by | Goes to |
| --- | --- | --- |
| `/v1/responses`, `/v1/conversations`, `/v1/messages`, `/v1/models` | `HTTPRoute/agentic-api` — longer path prefix | `Service/agentic-api` |
| anything with `Host: epp.gateway.internal` | `HTTPRoute/agentic-api-internal` — matching hostname | `InferencePool/${BASE_GUIDE_NAME}` |
| `/v1/chat/completions`, `/v1/completions`, `/health`, `/metrics`, … | the chart's own route, unchanged | `InferencePool/${BASE_GUIDE_NAME}` |

Precedence is specified *across* routes, not merely within one, so this does not depend on
creation order ([`gateway-api/apis/v1/httproute_types.go`](https://github.com/kubernetes-sigs/gateway-api/blob/main/apis/v1/httproute_types.go)):

> Across all rules specified on applicable Routes, precedence must be given to the match having:
> […] "Prefix" path match with largest number of characters. […] **If ties still exist** across
> multiple Routes, matching precedence MUST be determined [by] the oldest Route based on creation
> timestamp.

`/v1/responses` (13 characters) therefore beats `/` (1 character) regardless of which route was
created first, and a matching `hostnames` entry outranks both. This was verified on Istio 1.30.3
with Gateway API v1.6.2 CRDs, with the chart's route deliberately created first.

> [!NOTE]
> `/v1/models` is routed to `agentic-api` for the **Codex CLI**: with `?client_version=<ver>` set,
> `agentic-api` transforms the upstream list into the Codex model catalog. Without that query
> parameter it proxies the upstream response unchanged, so plain OpenAI clients see no
> difference — but this does put `agentic-api` on the `/v1/models` path for the base deployment
> too. Drop that one `matches` entry from `routes.yaml` to leave `/v1/models` with the
> `InferencePool`; Codex then has to be pointed at `Service/agentic-api:9000` directly.

- - -

## Installation

### 1. Environment

The base guide already exported `REPO_ROOT`, `NAMESPACE` and its own `GUIDE_NAME`, so run this in
the same shell and it mostly configures itself:

| Variable | Where it comes from |
| --- | --- |
| `REPO_ROOT` | same expression as the base guide, so re-running it is a no-op |
| `BASE_GUIDE_NAME` | inherited from the base guide's `GUIDE_NAME`. This guide needs a separate name for it because it reassigns `GUIDE_NAME` to `agentic-api` for its own paths, which is why `BASE_GUIDE_NAME` is declared first |
| `NAMESPACE` | inherited. `${NAMESPACE:-…}` keeps yours rather than forcing the default below, which matters whenever the base guide was deployed somewhere else |
| `GUIDE_NAME` | this guide, `agentic-api` |
| `MODE` | your choice, and it must match how the base guide's router was installed |
| `PROVIDER_NAME` | must match the provider the base guide was installed with. Unlike the two above it is a fixed choice rather than inherited, so check it against the base release before editing: `helm get values ${BASE_GUIDE_NAME} -n ${NAMESPACE} \| grep -A1 provider` |

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export BASE_GUIDE_NAME=${BASE_GUIDE_NAME:-${GUIDE_NAME:-pd-disaggregation}}
export NAMESPACE=${NAMESPACE:-llm-d-pd-disaggregation}
export GUIDE_NAME=agentic-api
export MODE=standalone # options: standalone, gateway
export PROVIDER_NAME=none # options: none, gke, agentgateway, istio
```
<!-- guide:env.static end -->

Confirm the resolved values are the ones you expect before going further; an inherited
`BASE_GUIDE_NAME` or `NAMESPACE` that is wrong shows up here rather than as a confusing failure
later:

```bash
echo "base guide: ${BASE_GUIDE_NAME}   namespace: ${NAMESPACE}   mode: ${MODE}   provider: ${PROVIDER_NAME}"
```

### 2. Confirm the base guide

The pre-flight check below reports a missing tool-calling flag before anything is deployed:

<!-- guide:prerequisites.base_guide.common start -->
```bash
# The base guide must already be deployed, per its own README -- unmodified.
# This extension never reinstalls or upgrades the router.
kubectl get svc ${BASE_GUIDE_NAME}-epp -n ${NAMESPACE}

# agentic-api cannot drive tool calls unless the base guide's vLLM was
# started with the tool-calling flags. They are CLI-only -- vLLM has no
# environment-variable equivalent -- so check the running pods rather than
# trusting the overlay. Querying pods by label covers every topology
# (Deployment, LeaderWorkerSet, DisaggregatedSet) and any container order.
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${BASE_GUIDE_NAME} -o yaml \
  | grep -q -- "--enable-auto-tool-choice" \
  || echo "WARNING: no --enable-auto-tool-choice on the ${BASE_GUIDE_NAME} model servers. Verification test [3/4] will fail. See the Prerequisites section of the guide README."
```
<!-- guide:prerequisites.base_guide.common end -->

**Gateway Mode only** — confirm the `InferencePool`, the base chart's `HTTPRoute`, and the `Gateway` are present:

<!-- guide:prerequisites.base_guide.gateway start -->
```bash
# only when MODE=gateway:
# Gateway Mode additionally needs the InferencePool, the chart's own
# HTTPRoute (which stays untouched), and a Gateway from
# guides/recipes/gateway/<provider>.
kubectl get inferencepool ${BASE_GUIDE_NAME} -n ${NAMESPACE}
kubectl get httproute ${BASE_GUIDE_NAME} -n ${NAMESPACE}
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```
<!-- guide:prerequisites.base_guide.gateway end -->


### 3. Create the PostgreSQL credentials

The password is generated locally and only ever stored in the Secret (reusing the existing Secret on reruns so it stays in sync with an already-initialized PVC).

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
# The password is generated here and never stored outside the Secret. The
# database-url uses the Service's short name, so the Secret is not tied to
# any particular namespace. Reuse the existing Secret on reruns so its
# password stays in sync with an already-initialized PostgreSQL PVC.
if ! kubectl get secret agentic-api-postgres -n ${NAMESPACE} >/dev/null 2>&1; then
  PGPASS=$(openssl rand -hex 16)
  kubectl create secret generic agentic-api-postgres -n ${NAMESPACE} \
    --from-literal=password="${PGPASS}" \
    --from-literal=database-url="postgres://postgres:${PGPASS}@agentic-api-postgres:5432/agentic_api"
fi
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

### 4. Deploy PostgreSQL

<!-- guide:deploy.postgres start -->
```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/postgres.yaml
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api-postgres --timeout=120s
```
<!-- guide:deploy.postgres end -->

### 5. Point agentic-api at the base guide's router

#### Standalone Mode

<!-- guide:deploy.api_base.standalone start -->
```bash
# only when MODE=standalone:
# Standalone Mode: the base guide's EPP runs an Envoy sidecar, so its
# Service speaks plain HTTP on :80 and agentic-api can dial it by DNS.
export LLM_API_BASE=${BASE_GUIDE_NAME}-epp.${NAMESPACE}.svc.cluster.local:80
```
<!-- guide:deploy.api_base.standalone end -->

#### Gateway Mode

<!-- guide:deploy.api_base.gateway start -->
```bash
# only when MODE=gateway:
# Gateway Mode: the EPP has no HTTP listener (only ext_proc on :9002), so
# agentic-api's upstream call goes back through the Gateway. The hostname
# is what keeps that from looping -- see manifests/gateway/routes.yaml.
export LLM_API_BASE=epp.gateway.internal
```
<!-- guide:deploy.api_base.gateway end -->

### 6. Resolve the Gateway address (Gateway Mode only)

<!-- guide:deploy.gateway_address start -->
```bash
# only when MODE=gateway:
kubectl wait --for=condition=Programmed gateway/llm-d-inference-gateway \
  -n ${NAMESPACE} --timeout=300s

# hostAliases requires a literal IP, and providers publish their Gateway
# address differently: GKE reports type: IPAddress (an external LoadBalancer
# IP), Istio reports type: Hostname (the Gateway Service FQDN, because the
# recipe creates a ClusterIP Service). Key off the reported type rather than
# PROVIDER_NAME so this holds for any provider.
GW_TYPE=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].type}')
GW_ADDR=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
if [ "${GW_TYPE}" = "Hostname" ]; then
  export GATEWAY_SVC=${GW_ADDR%%.*}
  export GATEWAY_IP=$(kubectl get svc ${GATEWAY_SVC} -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
else
  export GATEWAY_SVC=""
  export GATEWAY_IP=${GW_ADDR}
fi

# Must print a bare IP. A hostname here fails the next step with
# `spec.template.spec.hostAliases[0].ip: Invalid value: ... must be a valid IP address`.
echo "Gateway address: [${GATEWAY_IP}]  (reported as ${GW_TYPE})"
```
<!-- guide:deploy.gateway_address end -->

> [!IMPORTANT]
> `${GATEWAY_IP}` must print a bare IP. If it holds a hostname such as
> `llm-d-inference-gateway-istio.<ns>.svc.cluster.local`, the next step fails with
> `spec.template.spec.hostAliases[0].ip: Invalid value: ... must be a valid IP address`.

### 7. Deploy agentic-api

The manifests carry `${…}` placeholders for the values resolved above, so they are rendered with
`kubectl kustomize` and piped through `envsubst` — the same pattern as
[`guides/tiered-prefix-cache`](../tiered-prefix-cache/README.md).

#### Standalone Mode

<!-- guide:deploy.standalone start -->
```bash
# only when MODE=standalone:
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/base \
  | envsubst '${LLM_API_BASE}' \
  | kubectl apply -n ${NAMESPACE} -f -
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
```
<!-- guide:deploy.standalone end -->

#### Gateway Mode

`manifests/gateway-gke` is `manifests/gateway` plus the `networking.gke.io` policies for
`Service/agentic-api`; the base guide's own router chart renders the equivalent policies for the
`InferencePool` when installed with `provider.name=gke`.

**Istio / agentgateway / other providers:**

<!-- guide:deploy.gateway.default start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=none or agentgateway or istio:
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/gateway \
  | envsubst '${LLM_API_BASE} ${GATEWAY_IP} ${BASE_GUIDE_NAME}' \
  | kubectl apply -n ${NAMESPACE} -f -
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
```
<!-- guide:deploy.gateway.default end -->

**GKE (`PROVIDER_NAME=gke`):**

<!-- guide:deploy.gateway.gke start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=gke:
# gateway-gke adds the networking.gke.io policies for Service/agentic-api on
# top of everything in manifests/gateway.
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/gateway-gke \
  | envsubst '${LLM_API_BASE} ${GATEWAY_IP} ${BASE_GUIDE_NAME}' \
  | kubectl apply -n ${NAMESPACE} -f -
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
```
<!-- guide:deploy.gateway.gke end -->

- - -

## Verification

### Endpoint

#### Standalone Mode

<!-- guide:verify.endpoint.standalone start -->
```bash
# only when MODE=standalone:
kubectl port-forward --address 127.0.0.1 -n ${NAMESPACE} svc/agentic-api 9000:9000 &
PF_PID=$!
sleep 3
kill -0 ${PF_PID}
export AGENTIC_API_BASE_URL=http://127.0.0.1:9000
```
<!-- guide:verify.endpoint.standalone end -->

#### Gateway Mode

**GKE (`PROVIDER_NAME=gke`):**

<!-- guide:verify.endpoint.gateway.gke start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=gke:
# GATEWAY_IP is an external LoadBalancer IP, reachable directly.
export AGENTIC_API_BASE_URL=http://${GATEWAY_IP}
```
<!-- guide:verify.endpoint.gateway.gke end -->

**Istio / agentgateway / other providers:**

<!-- guide:verify.endpoint.gateway.clusterip start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=none or agentgateway or istio:
# The Gateway Service is ClusterIP, so reach it through a port-forward.
# GATEWAY_SVC was resolved in deploy.gateway_address above.
kubectl port-forward --address 127.0.0.1 -n ${NAMESPACE} svc/${GATEWAY_SVC} 8080:80 &
PF_PID=$!
sleep 3
kill -0 ${PF_PID}
export AGENTIC_API_BASE_URL=http://127.0.0.1:8080
```
<!-- guide:verify.endpoint.gateway.clusterip end -->

### Run the checks

#### Standalone Mode

<!-- guide:verify.tests.standalone start -->
```bash
# only when MODE=standalone:
python3 ${REPO_ROOT}/guides/${GUIDE_NAME}/verify.py --base-url ${AGENTIC_API_BASE_URL}
kill ${PF_PID:-} 2>/dev/null || true
```
<!-- guide:verify.tests.standalone end -->

#### Gateway Mode

<!-- guide:verify.tests.gateway start -->
```bash
# only when MODE=gateway:
# --skip-health is required in Gateway Mode: /health and /ready are not in
# the agentic route, so they fall through to the InferencePool.
# GKE Gateway Mode talks to the external IP directly and starts no
# port-forward, so PF_PID may legitimately be unset.
python3 ${REPO_ROOT}/guides/${GUIDE_NAME}/verify.py --base-url ${AGENTIC_API_BASE_URL} --skip-health
kill ${PF_PID:-} 2>/dev/null || true
```
<!-- guide:verify.tests.gateway end -->

[`verify.py`](verify.py) needs only the Python 3 standard library, discovers the served model from
`/v1/models`, and runs four end-to-end tests. All four are entirely client-side, including the
webhook receiver in `[3/4]`, so a port-forward is sufficient — nothing in the cluster dials back
to your machine.

1. **Health & model discovery `[1/4]`** — `/health` and `/ready` (Standalone Mode only) plus
   `GET /v1/models`.
2. **Stateful HTTP `/v1/responses` `[2/4]`** — stores a secret code with `store: true`, then sends
   a follow-up carrying *only* `previous_response_id` and no client-side history, verifying that
   `agentic-api` rehydrated the prior turn from PostgreSQL.
3. **Webhook mode & stateful tool loop `[3/4]`** — starts a local webhook listener, sends a
   request with a `function` tool, dispatches the resulting `function_call` to the listener, and
   returns the `function_call_output` via `previous_response_id`. This is the test that fails if
   the base guide's model server lacks the tool-calling flags, and it says so.
4. **WebSocket mode `[4/4]`** — upgrades an RFC 6455 connection to `ws://<endpoint>/v1/responses`,
   sends a `response.create` frame and verifies streaming completion (pass `--skip-websocket` to
   explicitly skip this check when testing through an HTTP-only proxy).

- - -

## Connecting Coding Harnesses

Once [`agentic-api`](https://github.com/vllm-project/agentic-api) is reachable at `${AGENTIC_API_BASE_URL}`, you can attach coding harnesses (such as OpenAI Codex CLI or Claude Code) to the deployed endpoint. See the [vllm-project/agentic-api repository](https://github.com/vllm-project/agentic-api) and its [Harness CLI Testing guide](https://github.com/vllm-project/agentic-api/blob/main/docs/guides/harness-cli-testing.md) for supported harnesses, configuration, and usage instructions.

- - -

## Cleanup

Removing the extension leaves the base guide exactly as it was.

#### Standalone Mode

<!-- guide:cleanup.workload.standalone start -->
```bash
# only when MODE=standalone:
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/base \
  | envsubst '${LLM_API_BASE}' \
  | kubectl delete -n ${NAMESPACE} -f - --ignore-not-found=true
```
<!-- guide:cleanup.workload.standalone end -->

#### Gateway Mode (Istio / agentgateway / other providers)

<!-- guide:cleanup.workload.gateway start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=none or agentgateway or istio:
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/gateway \
  | envsubst '${LLM_API_BASE} ${GATEWAY_IP} ${BASE_GUIDE_NAME}' \
  | kubectl delete -n ${NAMESPACE} -f - --ignore-not-found=true
```
<!-- guide:cleanup.workload.gateway end -->

#### Gateway Mode (GKE)

<!-- guide:cleanup.workload.gateway_gke start -->
```bash
# only when MODE=gateway and PROVIDER_NAME=gke:
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/gateway-gke \
  | envsubst '${LLM_API_BASE} ${GATEWAY_IP} ${BASE_GUIDE_NAME}' \
  | kubectl delete -n ${NAMESPACE} -f - --ignore-not-found=true
```
<!-- guide:cleanup.workload.gateway_gke end -->

#### PostgreSQL

<!-- guide:cleanup.postgres start -->
```bash
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/postgres.yaml --ignore-not-found=true
kubectl delete secret agentic-api-postgres -n ${NAMESPACE} --ignore-not-found=true
# The PVC is deleted by the manifest above; the PV's fate follows its
# StorageClass reclaim policy.
```
<!-- guide:cleanup.postgres end -->
