# Omni Model Serving

## Overview

This guide serves an **omni model**, a single checkpoint that takes text, images, audio, or video in and produces text *and* non-text output, behind the llm-d Router. One `InferencePool` of [vLLM-Omni](https://github.com/vllm-project/vllm-omni) pods fronts every OpenAI endpoint the model serves: `/v1/chat/completions` (with `modalities: ["text", "audio"]` for spoken answers), and, for checkpoints that support them, `/v1/audio/speech` and `/v1/images/generations`.

Two things make an omni pool different from a text pool:

- **One pool, several endpoints.** The router's request parser understands the chat, speech, and image-generation request bodies, so a single router and pool serve all of them. When an omni pool shares a Kubernetes Gateway with other pools, exact-path `HTTPRoute` rules send the modality endpoints to the omni pool (see [Split endpoints across pools](#split-endpoints-across-pools-gateway-mode)).
- **Load-based routing.** Audio and image generation are not prompt-length-bound text generation, and the Qwen3-Omni pipeline runs with prefix caching off, so prefix-cache and token-load scorers have no signal. The router uses the `queue-scorer` alone and picks the pod with the shortest queue.

The default deployment serves `Qwen/Qwen3-Omni-30B-A3B-Instruct` on **1 replica with 2 NVIDIA GPUs**: vLLM-Omni's default deploy config runs the thinker (text) stage on the first GPU, and the talker and code2wav (speech) stages on the second.

> [!WARNING]
> Omni serving is **experimental**. The routing configuration is an early baseline, and the manifests may change in upcoming releases.

### Why vLLM-Omni

Standard vLLM generates **text output only**: even when a model accepts images or audio (for example Qwen3-VL, see [Multimodal Model Serving](../multimodal-serving/README.md)), the response is text. Models whose output is audio or images need [vLLM-Omni](https://github.com/vllm-project/vllm-omni), which extends vLLM with multi-stage pipelines (for Qwen3-Omni: thinker → talker → code2wav) and the endpoints that return audio or images.

| Engine | Input | Output | Example models |
| --- | --- | --- | --- |
| **vLLM** | Text, images, audio, video | **Text only** | Qwen3-VL, Llama |
| **vLLM-Omni** | Text, images, audio, video | **Text, audio, images** | Qwen3-Omni, Qwen3-TTS, FLUX |

Which endpoints a vLLM-Omni pool answers depends on the checkpoint:

| Model | Endpoints |
| --- | --- |
| **Qwen3-Omni** (this guide) | `/v1/chat/completions`, with text and spoken (`modalities: ["text", "audio"]`) answers |
| **Qwen3-TTS** and other dedicated TTS models | `/v1/audio/speech` |
| **FLUX** and other diffusion models | `/v1/images/generations` |

To serve one endpoint per model instead, see the experimental single-endpoint guides: [text-to-speech](../diffusion-serving/text-to-speech/README.md), [text-to-image](../diffusion-serving/text-to-image/README.md), and [image-to-image](../diffusion-serving/image-to-image/README.md).

### Architecture

```text
Client → Proxy (Envoy sidecar) → Router (EPP: queue-scorer) → Omni pool pod
           /v1/chat/completions      ┐
           /v1/audio/speech          ├─ parsed by the router, all sent to the same pool
           /v1/images/generations    ┘
```

The model server overlay creates one `Deployment` whose pods carry the `llm-d.ai/guide=omni-serving` label that the `InferencePool` selects. Every pod serves the same model and endpoints, so the router needs no filter: each request goes to the pod with the shortest queue. With more than one replica, that spreads long-running audio and image generations across the pool instead of round-robining them.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM-Omni | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-Omni-30B-A3B-Instruct` | 🟡 community | H100 80 GB reference · 1 replica × 2 GPUs (thinker on GPU 0, talker + code2wav on GPU 1) · `INFRA_PROVIDER=base` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default: 1 replica with 2 NVIDIA GPUs with 80 GB of HBM, e.g. H100).

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

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
export GUIDE_NAME=omni-serving
export NAMESPACE=llm-d-omni-serving
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllmomni # options: vllmomni
export INFRA_PROVIDER=base # options: base
export MODEL=Qwen/Qwen3-Omni-30B-A3B-Instruct # the model the overlay serves (table above)
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

The [router values](router/omni-serving.values.yaml) schedule every request with the `queue-scorer` and `max-score-picker` only.

> [!NOTE]
> The router parses `/v1/audio/speech` requests since [llm-d-router#2484](https://github.com/llm-d/llm-d-router/pull/2484), which landed after the v0.10.0 release. The default chart version (`${ROUTER_CHART_VERSION}` = `v0`) deploys the `main`-tagged router image, which includes it.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. A single omni pool needs no `HTTPRoute`: the standalone proxy sends every path to the router. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router) and [Split endpoints across pools](#split-endpoints-across-pools-gateway-mode) below.

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

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay.** It runs `vllm serve Qwen/Qwen3-Omni-30B-A3B-Instruct --omni` on the `vllm/vllm-omni` image (the shared [`gpu-vllm-omni` image component](../recipes/modelserver/components/images/gpu-vllm-omni/release/kustomization.yaml)):

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

To serve another vLLM-Omni checkpoint, change the model in [`patch-vllm-omni.yaml`](modelserver/gpu/vllmomni/base/patch-vllm-omni.yaml) and size the GPU count to its deploy config; set `MODEL` to match.

### 3. Enable Monitoring (optional)

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites) and `MONITORING=true` when deploying the router):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### Split endpoints across pools (Gateway Mode)

Path-based routing *across pools* is the one part of this path that needs a Kubernetes Gateway: a standalone router fronts a single pool. A typical setup puts a text model and a speech or image model behind one Gateway, sending the modality endpoints to the vLLM-Omni pool and everything else to the text pool:

```text
/v1/audio/speech         (Exact)                  → omni-serving pool
/v1/audio/transcriptions (Exact)                  → omni-serving pool
/v1/images/generations   (Exact)                  → omni-serving pool
/v1/chat/completions     (PathPrefix / catch-all) → text pool
```

Exact-path matches take precedence over `PathPrefix` matches per the Gateway API specification, so the modality requests reach the vLLM-Omni pool regardless of the catch-all. To set it up:

1. Deploy the shared `llm-d-inference-gateway` and the text pool with Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router). Its router chart creates the `PathPrefix: /` catch-all `HTTPRoute`.
2. Install this guide's router with the Gateway chart (`${ROUTER_GATEWAY_CHART}`, `--set provider.name=<your gateway provider>`) instead of the standalone chart, leaving out `--set httpRoute.create=true` so it does not add a second catch-all, and deploy the model server as above.
3. Remove the path matches your model does not serve from [`httproutes.yaml`](httproutes.yaml), then apply it:

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/httproutes.yaml
```

> [!NOTE]
> Path matching splits traffic by endpoint, not by model. It fits TTS and diffusion checkpoints, whose endpoints differ from the text pool's. Qwen3-Omni returns spoken answers through `/v1/chat/completions`, the path the text pool owns, so behind a shared Gateway give it its own endpoint (a separate namespace and Gateway listener, as the [single-endpoint guides](../diffusion-serving/README.md) do) rather than a path rule. The same applies to two models that serve the same endpoint, such as two TTS models on `/v1/audio/speech`.

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a text chat request:**

<!-- guide:verify.tests.chat start -->
```bash
# Text in, text out
kubectl run chat-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/chat/completions" -H "Content-Type: application/json" \
    -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"What can you do, in one sentence?\"}], \"modalities\": [\"text\"], \"max_tokens\": 64}" \
    | jq -r ".choices[0].message.content"'
```
<!-- guide:verify.tests.chat end -->

### 3. Verify omni serving

The text request only shows that the thinker stage answers. Ask for a spoken answer to exercise the whole pipeline, then probe the other endpoint paths and check that each request reached the omni pool.

**Request text and audio output** from `/v1/chat/completions`:

<!-- guide:verify.tests.chat_audio start -->
```bash
# Text in, text and speech out: the thinker writes the answer, the
# talker and code2wav stages turn it into audio (base64 WAV)
kubectl run chat-audio-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/chat/completions" -H "Content-Type: application/json" \
    -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"Say hello to llm-d in one short sentence.\"}], \"modalities\": [\"text\", \"audio\"]}" \
    | jq -r ".choices[] | if .message.audio then \"audio: \(.message.audio.data | length) base64 characters\" else \"text: \(.message.content)\" end"'
```
<!-- guide:verify.tests.chat_audio end -->

Expect two lines: `text:` with the answer, and `audio:` with the size of the base64-encoded WAV the talker and code2wav stages generated. A missing `audio:` line means the speech stages did not run: check the model server logs for the talker stage.

**Probe the speech and image-generation paths:**

<!-- guide:verify.tests.endpoints start -->
```bash
# One request per OpenAI endpoint path the pool fronts; the status
# code comes from whichever component answered
kubectl run endpoints-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -o /dev/null -w "/v1/audio/speech: HTTP %{http_code}\n" -X POST "http://${IP}/v1/audio/speech" -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"input\": \"llm-d routes omni requests.\", \"response_format\": \"wav\"}"
    curl -sS -o /dev/null -w "/v1/images/generations: HTTP %{http_code}\n" -X POST "http://${IP}/v1/images/generations" -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"prompt\": \"a red apple on a wooden table\", \"size\": \"512x512\", \"n\": 1}"'
```
<!-- guide:verify.tests.endpoints end -->

With Qwen3-Omni, both requests reach the model server and are refused there: Qwen3-Omni produces speech through chat completions, recent vLLM-Omni releases reserve `/v1/audio/speech` for dedicated TTS models, and it does not generate images. With a TTS or diffusion checkpoint (see [Why vLLM-Omni](#why-vllm-omni)) the same requests return `200` with audio or image data.

**Read the model server's access log** to confirm where the requests went:

<!-- guide:verify.tests.pod_logs start -->
```bash
# The model server's access log: every request above, by endpoint
# path and status, on the pods of the Omni pool
kubectl logs -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -c modelserver --tail=-1 --prefix \
  | grep -E '"POST /v1/(chat/completions|audio/speech|images/generations) HTTP' || true
```
<!-- guide:verify.tests.pod_logs end -->

Every request above appears on a pod of the omni pool (prefixed with its pod name), under its endpoint path and the status the model server returned: the router parsed each request body and scheduled it onto the pool, whatever the endpoint. With more than one replica, concurrent requests spread across the pods by queue length.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

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

If you applied the exact-path rules in Gateway Mode, also delete them:

```bash
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/httproutes.yaml --ignore-not-found=true
```
