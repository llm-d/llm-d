# Serve the Stateful OpenAI Responses API (Experimental)

[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-agentic-api-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-agentic-api-gke-acc-gpu-vllm-x.yaml)

## Overview

This guide serves the OpenAI-compatible **Responses API** (`/v1/responses`) with the state it implies: multi-turn conversations continued by `previous_response_id`, webhook tool loops, and WebSocket streaming. It is the well-lit path for agentic clients and coding harnesses that speak the Responses API, such as the OpenAI Codex CLI.

The llm-d Router and vLLM are stateless: every request has to carry the whole conversation. [vLLM Agentic API](https://github.com/vllm-project/agentic-api) (`vllm/agentic-api`) sits in front of the router, persists each response in PostgreSQL, and rehydrates the conversation when a request names a `previous_response_id`, so the client sends only the new turn. Each inference round it sends on to the router is a stateless request that the router places with prefix-cache and load-aware scoring, as in the [Optimized Baseline](../optimized-baseline/README.md).

The reference deployment reuses the Optimized Baseline router configuration and model servers (on NVIDIA GPU, two `Qwen/Qwen3-32B` replicas with tensor parallelism 2, started with vLLM's tool-calling flags) and adds:

| Added | What it is |
| --- | --- |
| `Deployment/agentic-api` + `Service/agentic-api:9000` | `vllm/agentic-api`, configured to use the router as its inference backend |
| `Deployment/agentic-api-postgres` + PVC | PostgreSQL 17, the state store behind `previous_response_id` continuation |

```mermaid
flowchart LR
    C["Client / verify.py<br>(HTTP /v1/responses, webhooks, WS)"]
    A["Service: agentic-api:9000"]
    PG[("PostgreSQL 17<br>agentic-api-postgres:5432")]
    R["Service: agentic-api-epp:80<br>(standalone Envoy + EPP)"]
    V["vLLM model servers"]

    C -->|"/v1/responses"| A
    A <-->|"rehydrate & persist"| PG
    A -->|"--llm-api-base"| R
    R -->|"prefix & load-aware selection"| V
```

The router runs in [Standalone Mode](../../docs/architecture/router/proxy.md): the router Service speaks plain HTTP on `:80`, and `agentic-api` dials it by DNS. To layer `agentic-api` onto a router another guide already deployed, see [Add agentic-api to Another Guide](#add-agentic-api-to-another-guide).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly):

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | H100 80 GB reference · the Optimized Baseline model servers, 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

`agentic-api` owns conversation state and delegates each inference round to vLLM's own stateless Responses API, so the model server must expose `/v1/responses` ([`ARCHITECTURE.md`](https://github.com/vllm-project/agentic-api/blob/main/ARCHITECTURE.md), ADR-01 §1.1). SGLang and TensorRT-LLM model servers are therefore out of scope. Tool calls also need vLLM started with `--enable-auto-tool-choice` and the tool-call and reasoning parsers for its model; the Optimized Baseline NVIDIA GPU vLLM overlays are the ones that set them.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide, plus `envsubst`, `openssl` and Python 3 (standard library only).

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total).

- Ensure your cluster has a default StorageClass for the PostgreSQL PVC (1 GiB, `ReadWriteOnce`).

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

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
export GUIDE_NAME=agentic-api
export NAMESPACE=llm-d-agentic-api
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=base # options: base, gke
export MODEL=Qwen/Qwen3-32B # the model the overlays serve (table above)
export LLM_API_BASE=${GUIDE_NAME}-epp.${NAMESPACE}.svc.cluster.local:80
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

**Create the PostgreSQL credentials.** The password is generated locally and only ever stored in the Secret; reruns reuse the existing Secret so it stays in sync with an already-initialized PVC:

<!-- guide:prerequisites.postgres_secret start -->
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
<!-- guide:prerequisites.postgres_secret end -->

## Installation Instructions

### 1. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router. This guide uses the Optimized Baseline router configuration, which selects model server pods labeled `llm-d.ai/guide=optimized-baseline`:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files: the Optimized Baseline router configuration,
# which selects the model servers deployed below
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml"
```
<!-- guide:deploy.router_values end -->

**Deploy the router** in Standalone Mode, with an Envoy sidecar in front of the router. Its helm release name, `${GUIDE_NAME}`, names the `Service/agentic-api-epp` that `LLM_API_BASE` points at:

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your infrastructure provider. Each overlay is the [Optimized Baseline](../optimized-baseline/README.md#2-deploy-the-model-server) model server, which starts vLLM with `--enable-auto-tool-choice`, `--tool-call-parser=hermes` and `--reasoning-parser=qwen3`:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

### 3. Deploy PostgreSQL

<!-- guide:deploy.postgres start -->
```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/postgres.yaml
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api-postgres --timeout=120s
```
<!-- guide:deploy.postgres end -->

### 4. Deploy agentic-api

The manifest carries a `${LLM_API_BASE}` placeholder for the router address, so it is rendered with `kubectl kustomize` and piped through `envsubst`:

<!-- guide:deploy.agentic_api start -->
```bash
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/base \
  | envsubst '${LLM_API_BASE}' \
  | kubectl apply -n ${NAMESPACE} -f -
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
```
<!-- guide:deploy.agentic_api end -->

### 5. Wait for the Model Servers

<!-- guide:deploy.ready start -->
```bash
# The model servers become Available once vLLM has loaded the model
kubectl wait -n ${NAMESPACE} --for=condition=Available deployment \
  -l llm-d.ai/guide=optimized-baseline --timeout=30m
```
<!-- guide:deploy.ready end -->

## Verification

### 1. Reach agentic-api

<!-- guide:verify.endpoint.standalone start -->
```bash
kubectl port-forward --address 127.0.0.1 -n ${NAMESPACE} svc/agentic-api 9000:9000 &
PF_PID=$!
sleep 3
kill -0 ${PF_PID}
export AGENTIC_API_BASE_URL=http://127.0.0.1:9000
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send a Test Request

<!-- guide:verify.tests.request start -->
```bash
curl -sS -X POST ${AGENTIC_API_BASE_URL}/v1/responses \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"${MODEL}\", \"input\": \"How are you today?\"}"
```
<!-- guide:verify.tests.request end -->

The response is a `response` object with an `id` starting with `resp_`; pass that `id` as `previous_response_id` on the next request to continue the conversation without resending it.

### 3. Verify the Mechanism

A served request only proves the stack is up. [`verify.py`](verify.py) checks that `agentic-api` keeps state and drives tool loops:

<!-- guide:verify.tests.mechanism start -->
```bash
python3 ${REPO_ROOT}/guides/${GUIDE_NAME}/verify.py --base-url ${AGENTIC_API_BASE_URL}
kill ${PF_PID:-} 2>/dev/null || true
```
<!-- guide:verify.tests.mechanism end -->

It needs only the Python 3 standard library, discovers the served model from `/v1/models`, and runs four end-to-end tests. All four are entirely client-side, including the webhook receiver in `[3/4]`, so a port-forward is sufficient: nothing in the cluster dials back to your machine.

1. **Health & model discovery `[1/4]`**: `/health`, `/ready` and `GET /v1/models`.
2. **Stateful HTTP `/v1/responses` `[2/4]`**: stores a secret code with `store: true`, then sends a follow-up carrying *only* `previous_response_id` and no client-side history, verifying that `agentic-api` rehydrated the prior turn from PostgreSQL.
3. **Webhook mode & stateful tool loop `[3/4]`**: starts a local webhook listener, sends a request with a `function` tool, dispatches the resulting `function_call` to the listener, and returns the `function_call_output` via `previous_response_id`. This is the test that fails if the model server lacks the tool-calling flags, and it says so.
4. **WebSocket mode `[4/4]`**: upgrades an RFC 6455 connection to `ws://<endpoint>/v1/responses`, sends a `response.create` frame and verifies streaming completion (pass `--skip-websocket` to explicitly skip this check when testing through an HTTP-only proxy).

These steps run nightly on GKE with one model server replica:
[`nightly-e2e-agentic-api-gke-acc-gpu-vllm-x.yaml`](../../.github/workflows/nightly-e2e-agentic-api-gke-acc-gpu-vllm-x.yaml)
deploys this guide from its `guide.yaml` with
[`scripts/nightly-deploy-gke.sh`](scripts/nightly-deploy-gke.sh) and then runs this section.

## Add agentic-api to Another Guide

`agentic-api` needs only a router Service to send inference to, so it also layers onto a guide you already run, such as [P/D Disaggregation](../pd-disaggregation/README.md) or [Wide Expert Parallelism](../wide-ep/README.md), provided its model server is vLLM. The other guide stays as its own README leaves it.

After [configuring the environment](#configure-the-environment), point this guide at the other guide's namespace, router and model. The router Service is named after that guide's helm release, its `GUIDE_NAME`:

```bash
export NAMESPACE=llm-d-pd-disaggregation
export LLM_API_BASE=pd-disaggregation-epp.${NAMESPACE}.svc.cluster.local:80
export MODEL=openai/gpt-oss-120b
```

Then create the PostgreSQL credentials and run installation steps 3 and 4 and the verification; skip steps 1, 2 and 5, which the other guide covers. To remove it, run only the `agentic-api` and PostgreSQL [cleanup](#cleanup) steps.

The other guide's vLLM must be started with `--enable-auto-tool-choice` and the parsers for its model, or verification test `[3/4]` fails. These are CLI flags with no environment-variable equivalent, so add them to the model server overlay **before** you deploy that guide, next to the other `vllm serve` args. Where the overlay runs vLLM from a `command: ["/bin/bash", "-c"]` script, they go on the `vllm serve` line inside the script. These overlays already set them:

| Model | Flags | Already set by |
| --- | --- | --- |
| `openai/gpt-oss-120b` | `--enable-auto-tool-choice`<br>`--tool-call-parser=openai`<br>`--reasoning-parser=openai_gptoss` | [`pd-disaggregation`](../pd-disaggregation/modelserver/gpu/vllm/base/disaggregatedset.yaml) (every `gpu/vllm` overlay) and [`p2p-kv-cache-sharing`](../p2p-kv-cache-sharing/modelserver/gpu/vllm/base/patch-vllm.yaml) (`gpu/vllm`) |
| `Qwen/Qwen3-32B` | `--enable-auto-tool-choice`<br>`--tool-call-parser=hermes`<br>`--reasoning-parser=qwen3` | [`optimized-baseline`](../optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml) and [`precise-prefix-cache-routing`](../precise-prefix-cache-routing/modelserver/gpu/vllm/base/patch-vllm.yaml) (`gpu/vllm`), and [`pd-disaggregation`](../pd-disaggregation/modelserver/tpu/v6/vllm/base/patch-prefill.yaml) (`tpu/v6`). Missing on `tiered-prefix-cache` |
| `Qwen/Qwen3-0.6B` | `--enable-auto-tool-choice`<br>`--tool-call-parser=hermes`<br>`--reasoning-parser=qwen3` | [`p2p-kv-cache-sharing`](../p2p-kv-cache-sharing/modelserver/xpu/vllm/base/patch-vllm.yaml) (`xpu/vllm`) |
| `Qwen/Qwen3.5-397B-A17B-FP8` | `--enable-auto-tool-choice`<br>`--tool-call-parser=qwen3_xml`<br>`--reasoning-parser=qwen3` | [`pd-disaggregation`](../pd-disaggregation/modelserver/tpu/v7/vllm/base/patch-prefill.yaml) (`tpu/v7`). Its chat template emits XML-style tool calls, so `hermes` does not apply |
| `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | `--enable-auto-tool-choice`<br>`--tool-call-parser=qwen3_xml` | [`pd-disaggregation`](../pd-disaggregation/modelserver/tpu/v7-dynamic-slice/vllm/base/disaggregatedset.yaml) (`tpu/v7-dynamic-slice`). No reasoning parser: the model emits no reasoning blocks |
| `deepseek-ai/DeepSeek-R1-0528` | `--enable-auto-tool-choice`<br>`--tool-call-parser=deepseek_v3`<br>`--chat-template /vllm-workspace/examples/tool_chat_template_deepseekr1.jinja` | [`wide-ep`](../wide-ep/modelserver/gpu/vllm/base/disaggregatedset.yaml). The model's bundled chat template never renders the `tools` list, so vLLM's replacement is required; no reasoning parser, since vLLM lists `deepseek_r1` reasoning as [not supporting tool calling](https://github.com/vllm-project/vllm/blob/main/docs/features/reasoning_outputs.md) |
| `deepseek-ai/DeepSeek-V4-Pro` | `--enable-auto-tool-choice`<br>`--tool-call-parser=deepseek_v4`<br>`--reasoning-parser=deepseek_v4` | [`models/deepseek-v4`](../models/deepseek-v4/modelserver/gpu/vllm/base/disaggregatedset.yaml), both the `base` and `base-low-latency` overlays |
| `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` | `--enable-auto-tool-choice`<br>`--tool-call-parser=qwen3_coder`<br>`--reasoning-parser=nemotron_v3` | [`models/nemotron-3-ultra`](../models/nemotron-3-ultra/modelserver/gpu/vllm/gke/patch-prefill.yaml) |

[llm-d#2640](https://github.com/llm-d/llm-d/issues/2640) tracks the models and overlays that still omit them.

> [!WARNING]
> A wrong parser is worse than none: vLLM starts, accepts the request, and then tries to parse tool calls with the wrong grammar.

## Connecting Coding Harnesses

Once [`agentic-api`](https://github.com/vllm-project/agentic-api) is reachable at `${AGENTIC_API_BASE_URL}`, you can attach coding harnesses (such as OpenAI Codex CLI or Claude Code) to the deployed endpoint. See the [vllm-project/agentic-api repository](https://github.com/vllm-project/agentic-api) and its [Harness CLI Testing guide](https://github.com/vllm-project/agentic-api/blob/main/docs/guides/harness-cli-testing.md) for supported harnesses, configuration, and usage instructions.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.agentic_api start -->
```bash
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/base \
  | envsubst '${LLM_API_BASE}' \
  | kubectl delete -n ${NAMESPACE} -f - --ignore-not-found=true
```
<!-- guide:cleanup.agentic_api end -->

<!-- guide:cleanup.postgres start -->
```bash
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/postgres.yaml --ignore-not-found=true
kubectl delete secret agentic-api-postgres -n ${NAMESPACE} --ignore-not-found=true
# The PVC is deleted by the manifest above; the PV's fate follows its
# StorageClass reclaim policy.
```
<!-- guide:cleanup.postgres end -->

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
