# Omni Model Serving

## Overview

This guide serves models whose output is not only text (speech, images, or both) behind the llm-d Router, on [vLLM-Omni](https://github.com/vllm-project/vllm-omni) (and, for the image tasks, [SGLang](https://github.com/sgl-project/sglang)). The `TASK` variable picks what the pool serves:

| `TASK` | Model | Endpoints | Engines | Router scorer |
| --- | --- | --- | --- | --- |
| `omni` (default) | [`Qwen/Qwen3-Omni-30B-A3B-Instruct`](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct) | `/v1/chat/completions` (text and spoken answers) | vLLM-Omni | `queue-scorer` |
| `text-to-image` | [`Qwen/Qwen-Image`](https://huggingface.co/Qwen/Qwen-Image) | `/v1/images/generations` | vLLM-Omni, SGLang | `active-request-scorer` |
| `image-to-image` | [`Qwen/Qwen-Image-Edit-2511`](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | `/v1/images/edits` (`multipart/form-data`) | vLLM-Omni, SGLang | `active-request-scorer` |
| `text-to-speech` | [`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | `/v1/audio/speech` | vLLM-Omni | `active-request-scorer` |

The tasks fall into two shapes:

- **An omni model: one checkpoint, many endpoints.** Qwen3-Omni takes text, images, audio, or video in and answers with text *and* speech from a multi-stage pipeline (thinker → talker → code2wav). One `InferencePool` fronts every OpenAI endpoint the checkpoint serves. The router's request parser understands the chat, speech, and image-generation request bodies, so a single router and pool serve all of them; when the pool shares a Kubernetes Gateway with other pools, exact-path `HTTPRoute` rules send the modality endpoints to it (see [Split endpoints across pools](#split-endpoints-across-pools-gateway-mode)).
- **A single-endpoint generator: one model per pool.** A diffusion model (Qwen-Image, Qwen-Image-Edit) or a dedicated TTS model answers one endpoint. Each `TASK` deploys its own pool of 3 single-GPU replicas.

Either way the router **routes on load**.
Diffusion models generate by iterative denoising rather than autoregressive token generation, and the Qwen3-Omni pipeline runs with prefix caching off, so prefix-cache and token-load scorers have no signal.
The `omni` router picks the pod with the shortest queue (`queue-scorer`).
The single-endpoint routers track how many requests they have dispatched to each pod and not yet seen complete, and prefer the least busy one (`active-request-scorer`); they need no model-server metrics, which SGLang's diffusion backend does not publish. The Qwen3-TTS talker is autoregressive, so prefix-aware TTS routing is possible in principle and is tracked in [llm-d-router#2483](https://github.com/llm-d/llm-d-router/issues/2483).

### Why vLLM-Omni

Standard vLLM generates **text output only**: even when a model accepts images or audio (for example Qwen3-VL, see [Multimodal Model Serving](../multimodal-serving/README.md)), the response is text. Models whose output is audio or images need [vLLM-Omni](https://github.com/vllm-project/vllm-omni), which extends vLLM with multi-stage pipelines and the endpoints that return audio or images. Which endpoints a vLLM-Omni pod answers depends on the checkpoint: the `TASK` table above lists them for the models this guide deploys.

| Engine | Input | Output | Example models |
| --- | --- | --- | --- |
| **vLLM** | Text, images, audio, video | **Text only** | Qwen3-VL, Llama |
| **vLLM-Omni** | Text, images, audio, video | **Text, audio, images** | Qwen3-Omni, Qwen3-TTS, Qwen-Image, FLUX |
| **SGLang** (`sglang serve` → `sglang.multimodal_gen`) | Text, images | **Images** | Qwen-Image, Qwen-Image-Edit |

### Architecture

```text
Client → Proxy (Envoy sidecar) → Router (EPP) → pod of the TASK's pool
  TASK=omni            /v1/chat/completions (and the checkpoint's other endpoints)   queue-scorer
  TASK=text-to-image   /v1/images/generations                                       active-request-scorer
  TASK=image-to-image  /v1/images/edits                                             active-request-scorer
  TASK=text-to-speech  /v1/audio/speech                                             active-request-scorer
```

The model server overlay creates one `Deployment` whose pods carry the `llm-d.ai/guide=omni-serving` label that the `InferencePool` selects. Every pod serves the same model and endpoints, so the router needs no filter: each request goes to the least loaded pod. That spreads long-running audio and image generations across the pool instead of round-robining them.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM-Omni | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-Omni-30B-A3B-Instruct` | 🟡 community | 🟡 community | Served model is `TASK=omni`'s (others in the Overview table) · H100 80 GB reference · `omni`: 1 replica × 2 GPUs · `text-to-image`, `image-to-image`, `text-to-speech`: 3 replicas × 1 GPU · SGLang: `text-to-image` and `image-to-image` only |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration: 2 NVIDIA GPUs with 80 GB of HBM (e.g. H100) for `omni` (1 replica: thinker on the first GPU, talker and code2wav on the second), 3 for the other tasks (3 replicas × 1 GPU). Qwen-Image is a ~20B MMDiT plus a ~8B text encoder, about 57 GB in bf16, and fits one 80 GB GPU at full precision; Qwen3-TTS is small, so the H100 is a default there, not a requirement.

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

**Set the guide-specific environment variables.** Set `TASK` to what the pool serves and `MODEL` to that task's model (listed in the comment). `MODEL_SERVER=sglang` applies to `text-to-image` and `image-to-image` only:

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=omni-serving
export NAMESPACE=llm-d-omni-serving
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export TASK=omni # options: omni, text-to-image, image-to-image, text-to-speech
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllmomni # options: vllmomni, sglang
export INFRA_PROVIDER=base # options: base, gke
export MODEL=Qwen/Qwen3-Omni-30B-A3B-Instruct # the model the TASK's overlay serves: omni Qwen/Qwen3-Omni-30B-A3B-Instruct, text-to-image Qwen/Qwen-Image, image-to-image Qwen/Qwen-Image-Edit-2511, text-to-speech Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below). Every task layers its router values on the shared base:

<!-- guide:deploy.router_values.base start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
```
<!-- guide:deploy.router_values.base end -->

Then select your task (`TASK`):

<!-- tabs:start group=task -->
<details open>
<summary><b>Omni (Default)</b></summary>

The [`omni` router values](router/omni.values.yaml) schedule every request with the `queue-scorer` and `max-score-picker` only.

<!-- guide:deploy.router_values.omni start -->
```bash
# only when TASK=omni:
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/omni.values.yaml"
```
<!-- guide:deploy.router_values.omni end -->

</details>
<details>
<summary><b>Text-to-Image</b></summary>

The [`text-to-image` router values](router/text-to-image.values.yaml) schedule on the `active-request-scorer` and `max-score-picker`, and turn off the router's default metrics scraping (`injectDefaults: false`), because SGLang's diffusion backend publishes no metrics.

<!-- guide:deploy.router_values.text-to-image start -->
```bash
# only when TASK=text-to-image:
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/text-to-image.values.yaml"
```
<!-- guide:deploy.router_values.text-to-image end -->

</details>
<details>
<summary><b>Image-to-Image</b></summary>

The [`image-to-image` router values](router/image-to-image.values.yaml) are the `text-to-image` configuration on a pool of their own: `active-request-scorer`, `max-score-picker`, no metrics scraping.

<!-- guide:deploy.router_values.image-to-image start -->
```bash
# only when TASK=image-to-image:
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/image-to-image.values.yaml"
```
<!-- guide:deploy.router_values.image-to-image end -->

</details>
<details>
<summary><b>Text-to-Speech</b></summary>

The [`text-to-speech` router values](router/text-to-speech.values.yaml) are the same configuration on a pool of their own: `active-request-scorer`, `max-score-picker`, no metrics scraping.

<!-- guide:deploy.router_values.text-to-speech start -->
```bash
# only when TASK=text-to-speech:
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/text-to-speech.values.yaml"
```
<!-- guide:deploy.router_values.text-to-speech end -->

</details>
<!-- tabs:end -->

> [!NOTE]
> The router parses `/v1/audio/speech` requests since [llm-d-router#2484](https://github.com/llm-d/llm-d-router/pull/2484), which landed after the v0.10.0 release. The default chart version (`${ROUTER_CHART_VERSION}` = `v0`) deploys the `main`-tagged router image, which includes it.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router.
The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. A single pool needs no `HTTPRoute`: the standalone proxy sends every path to the router. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router) and, for `omni`, [Split endpoints across pools](#split-endpoints-across-pools-gateway-mode) below.

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

**Apply the Kustomize overlay** for your task. The overlays live at `modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TASK}/${INFRA_PROVIDER}/`; the `gke` variants add the [`disable-gke-nccl-tuner-patch`](../recipes/modelserver/components/disable-gke-nccl-tuner-patch) component on top of `base`.

<!-- tabs:start group=task -->
<details open>
<summary><b>Omni (Default)</b></summary>

Runs `vllm serve Qwen/Qwen3-Omni-30B-A3B-Instruct --omni` on the `vllm/vllm-omni` image (the shared [`gpu-vllm-omni` image component](../recipes/modelserver/components/images/gpu-vllm-omni/release/kustomization.yaml)), 1 replica × 2 GPUs:

<!-- guide:deploy.modelserver.omni start -->
```bash
# only when TASK=omni:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/omni/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.omni end -->

To serve another vLLM-Omni checkpoint, change the model in [`patch-vllm-omni.yaml`](modelserver/gpu/vllmomni/omni/base/patch-vllm-omni.yaml) and size the GPU count to its deploy config; set `MODEL` to match.

</details>
<details>
<summary><b>Text-to-Image</b></summary>

Serves `Qwen/Qwen-Image` on 3 replicas × 1 GPU, with vLLM-Omni ([`patch-vllm-omni.yaml`](modelserver/gpu/vllmomni/text-to-image/base/patch-vllm-omni.yaml)) or SGLang ([`patch-sglang.yaml`](modelserver/gpu/sglang/text-to-image/base/patch-sglang.yaml)):

<!-- guide:deploy.modelserver.text-to-image start -->
```bash
# only when TASK=text-to-image:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/text-to-image/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.text-to-image end -->

</details>
<details>
<summary><b>Image-to-Image</b></summary>

Serves `Qwen/Qwen-Image-Edit-2511` on 3 replicas × 1 GPU, with vLLM-Omni ([`patch-vllm-omni-edit.yaml`](modelserver/gpu/vllmomni/image-to-image/base/patch-vllm-omni-edit.yaml)) or SGLang ([`patch-sglang.yaml`](modelserver/gpu/sglang/image-to-image/base/patch-sglang.yaml)). An edit-capable checkpoint is required: `Qwen/Qwen-Image` reports task type `T2I` and rejects edits (`input_reference is not supported for T2I models`), and `Qwen/Qwen-Image-Edit-2511` reports `I2I` and rejects `/v1/images/generations`.

<!-- guide:deploy.modelserver.image-to-image start -->
```bash
# only when TASK=image-to-image:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/image-to-image/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.image-to-image end -->

</details>
<details>
<summary><b>Text-to-Speech</b></summary>

Serves `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` (a ~1.7B autoregressive talker plus a 12 Hz codec decoder) on vLLM-Omni, 3 replicas × 1 GPU ([`patch-vllm-omni-tts.yaml`](modelserver/gpu/vllmomni/text-to-speech/base/patch-vllm-omni-tts.yaml)):

<!-- guide:deploy.modelserver.text-to-speech start -->
```bash
# only when TASK=text-to-speech:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/text-to-speech/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.text-to-speech end -->

</details>
<!-- tabs:end -->

> [!NOTE]
> Deploy one task and one engine at a time per namespace. Every overlay carries the same `llm-d.ai/guide=omni-serving` label, which is what the `InferencePool` selects on, so two overlays in one namespace would join one pool.

### 3. Enable Monitoring (optional)

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites) and `MONITORING=true` when deploying the router). SGLang's diffusion backend publishes no metrics, so with `MODEL_SERVER=sglang` only the router's metrics are scraped:

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### Split endpoints across pools (Gateway Mode)

This section applies to `TASK=omni`. Path-based routing *across pools* is the one part of this path that needs a Kubernetes Gateway: a standalone router fronts a single pool. A typical setup puts a text model and a speech or image model behind one Gateway, sending the modality endpoints to the vLLM-Omni pool and everything else to the text pool:

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
> Path matching splits traffic by endpoint, not by model. It fits TTS and diffusion checkpoints, whose endpoints differ from the text pool's. Qwen3-Omni returns spoken answers through `/v1/chat/completions`, the path the text pool owns, so behind a shared Gateway give it its own endpoint (a separate namespace and Gateway listener, with the Gateway chart's own `HTTPRoute`) rather than a path rule. The same applies to two models that serve the same endpoint, such as two TTS models on `/v1/audio/speech`.

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

Send a request to your task's endpoint and check that a valid response comes back:

<!-- tabs:start group=task -->
<details open>
<summary><b>Omni (Default)</b></summary>

**Send a text chat request:**

<!-- guide:verify.tests.omni.chat start -->
```bash
# only when TASK=omni:
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
<!-- guide:verify.tests.omni.chat end -->

The text request only shows that the thinker stage answers. **Request text and audio output** to exercise the whole pipeline:

<!-- guide:verify.tests.omni.chat_audio start -->
```bash
# only when TASK=omni:
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
<!-- guide:verify.tests.omni.chat_audio end -->

Expect two lines: `text:` with the answer, and `audio:` with the size of the base64-encoded WAV the talker and code2wav stages generated. A missing `audio:` line means the speech stages did not run: check the model server logs for the talker stage.

</details>
<details>
<summary><b>Text-to-Image</b></summary>

**Generate an image** (`response_format: b64_json` returns the image inline):

<!-- guide:verify.tests.text-to-image start -->
```bash
# only when TASK=text-to-image:
# Text in, image out: report the format of the returned image
# (PNG from vLLM-Omni, JPEG from SGLang) and its size
kubectl run t2i-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/images/generations" -H "Content-Type: application/json" \
    -d "{\"model\": \"${MODEL}\", \"prompt\": \"a red apple on a wooden table\", \"size\": \"512x512\", \"n\": 1, \"response_format\": \"b64_json\"}" \
    | jq -r "if .data[0].b64_json then (.data[0].b64_json | (if startswith(\"iVBORw0KGgo\") then \"PNG\" elif startswith(\"/9j/\") then \"JPEG\" else \"unexpected\" end) + \" image, \(length) base64 characters\") else . end"'
```
<!-- guide:verify.tests.text-to-image end -->

Expect `PNG image, … base64 characters` from vLLM-Omni or `JPEG image, …` from SGLang. Anything else prints the full response body, which carries the server's error. SGLang's response also carries `peak_memory_mb`, `inference_time_s`, and `file_path` (a path inside the pod); neither engine returns token usage.

</details>
<details>
<summary><b>Image-to-Image</b></summary>

**Edit an image.** The request is `multipart/form-data`: an input image (a 64×64 red PNG embedded in the command) and an instruction:

<!-- guide:verify.tests.image-to-image start -->
```bash
# only when TASK=image-to-image:
# Image and instruction in (multipart/form-data), edited image out.
# The input is a 64x64 red PNG embedded here, so no download is needed
kubectl run i2i-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'echo "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsKZeS0BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEDgsqnc8OJg6Ln3AAAAAElFTkSuQmCC" | base64 -d > /tmp/input.png
    curl -sS -X POST "http://${IP}/v1/images/edits" \
      -F "image=@/tmp/input.png" -F "prompt=turn the red square into a blue circle" \
      -F "model=${MODEL}" -F "size=512x512" -F "n=1" \
    | jq -r "if .data[0].b64_json then (.data[0].b64_json | (if startswith(\"iVBORw0KGgo\") then \"PNG\" elif startswith(\"/9j/\") then \"JPEG\" else \"unexpected\" end) + \" image, \(length) base64 characters\") else . end"'
```
<!-- guide:verify.tests.image-to-image end -->

Expect `PNG image, … base64 characters` from vLLM-Omni or `JPEG image, …` from SGLang. A 512×512 edit takes about 15 seconds on an H100, at about 48 GB of GPU memory.

</details>
<details>
<summary><b>Text-to-Speech</b></summary>

**Synthesize speech.** Unlike the JSON image endpoints, a successful response body is the binary audio itself:

<!-- guide:verify.tests.text-to-speech start -->
```bash
# only when TASK=text-to-speech:
# Text in, speech out: the response body is the WAV file itself;
# a valid one starts with "RIFF"
kubectl run tts-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -o /tmp/speech.wav -w "HTTP %{http_code}, %{size_download} bytes, %{content_type}\n" \
      -X POST "http://${IP}/v1/audio/speech" -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"input\": \"llm-d routes speech requests to the least busy pod.\", \"voice\": \"vivian\", \"response_format\": \"wav\"}"
    echo "header: $(head -c 4 /tmp/speech.wav)"'
```
<!-- guide:verify.tests.text-to-speech end -->

Expect `HTTP 200` with a non-zero size and an audio content type, and `header: RIFF`, the start of a valid WAV file.

The endpoint also streams through the router: `"stream_format": "sse"` returns `speech.audio.delta` events with base64 audio chunks and a final `speech.audio.done` event carrying token usage, and `"stream_format": "audio"` returns raw PCM chunks as they are generated, with time-to-first-byte in the tens of milliseconds.
vLLM-Omni v0.28.0 and later also report usage for non-streamed responses in `x-vllm-omni-input-tokens`, `x-vllm-omni-output-tokens`, and `x-vllm-omni-total-tokens` headers (see the [speech API response format](https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/speech_api/#response-format)); the default image (v0.26.0) omits them.

</details>
<!-- tabs:end -->

### 3. Check where the requests landed

**Read the model server logs** to confirm that the router scheduled each request onto a pod of the pool:

<!-- guide:verify.tests.pod_logs start -->
```bash
# The model servers' logs: every request above, by endpoint path,
# on the pods of the pool (each line prefixed with its pod name)
kubectl logs -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -c modelserver --tail=-1 --prefix \
  | grep -E 'POST /v1/(chat/completions|images/generations|images/edits|audio/speech)' || true
```
<!-- guide:verify.tests.pod_logs end -->

Every request above appears on a pod of the pool (each line prefixed with its pod name) under its endpoint path: the router parsed the request body and scheduled it onto the pool, whatever the endpoint. Send several requests concurrently and they spread across the pods by load. The logs are the evidence that works for every engine; SGLang's diffusion backend publishes no metrics to read the same from.

## Cleanup

To remove the deployed components, delete your task's model server:

<!-- tabs:start group=task -->
<details open>
<summary><b>Omni (Default)</b></summary>

<!-- guide:cleanup.modelserver.omni start -->
```bash
# only when TASK=omni:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/omni/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver.omni end -->

If you applied the exact-path rules in Gateway Mode, also delete them:

```bash
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/httproutes.yaml --ignore-not-found=true
```

</details>
<details>
<summary><b>Text-to-Image</b></summary>

<!-- guide:cleanup.modelserver.text-to-image start -->
```bash
# only when TASK=text-to-image:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/text-to-image/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver.text-to-image end -->

</details>
<details>
<summary><b>Image-to-Image</b></summary>

<!-- guide:cleanup.modelserver.image-to-image start -->
```bash
# only when TASK=image-to-image:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/image-to-image/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver.image-to-image end -->

</details>
<details>
<summary><b>Text-to-Speech</b></summary>

<!-- guide:cleanup.modelserver.text-to-speech start -->
```bash
# only when TASK=text-to-speech:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/text-to-speech/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver.text-to-speech end -->

</details>
<!-- tabs:end -->

Then remove the router, the monitoring resources, and the namespace:

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
