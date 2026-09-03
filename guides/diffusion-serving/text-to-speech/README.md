# [Experimental] Text-to-Speech Guide

This guide deploys a text-to-speech (TTS) model behind the llm-d router and serves it over the OpenAI-compatible `POST /v1/audio/speech` endpoint.

> [!NOTE]
> Parsing `/v1/audio/speech` requests in the EPP landed in [llm-d-router#2484](https://github.com/llm-d/llm-d-router/pull/2484) after the v0.10.0 release. This guide requires a router release newer than v0.10.0.

Currently, this guide routes on load: the EPP tracks how many requests it has dispatched to each endpoint and not yet seen complete, and prefers the least busy one. The Qwen3-TTS talker stage is autoregressive, so prefix-cache-aware routing is possible in principle and is tracked as follow-up work in [llm-d-router#2483](https://github.com/llm-d/llm-d-router/issues/2483).

---

## Default Configuration

| Parameter        | Value                                                                                               |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| Default Model    | [Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)  |
| Replicas         | 3                                                                                                     |
| GPUs per replica | 1                                                                                                     |
| Total GPUs       | 3                                                                                                     |
| Accelerator      | NVIDIA H100 80GB                                                                                      |

Qwen3-TTS-12Hz-1.7B-CustomVoice is a ~1.7B autoregressive talker plus a 12Hz codec decoder. It is a small model and fits comfortably on a single GPU; the H100 is a default, not a requirement.

### Supported Hardware Backends

| Backend    | Directory                                     | Model                                  | Notes                                                            |
| ---------- | --------------------------------------------- | -------------------------------------- | ---------------------------------------------------------------- |
| NVIDIA GPU | `modelserver/gpu/vllmomni/${INFRA_PROVIDER}/` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | Default configuration (`INFRA_PROVIDER` options: `base`, `gke`)   |

---

## Prerequisites

1. Install the local client tooling using the [client setup guide](../../../helpers/client-setup/README.md).
2. Clone and check out the llm-d repository:

   ```bash
   export branch="main" # branch, tag, or commit hash
   git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   ```

3. Set up environment variables:

   ```bash
   source ${REPO_ROOT}/guides/env.sh
   export GUIDE_NAME="text-to-speech"
   export NAMESPACE=llm-d-diffusion-tts
   export ENGINE=vllmomni
   export INFRA_PROVIDER=gke # base | gke
   ```

4. Install the Gateway API Inference Extension CRDs:

   ```bash
   # GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
   kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
   ```

5. Create the namespace:

   ```bash
   kubectl create namespace ${NAMESPACE}
   ```

6. [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models. Qwen3-TTS is Apache-2.0, so the token is optional for the default model.
<!-- llm-d-cicd:skip start -->
   ```bash
   export HF_TOKEN=<your HuggingFace token>
   kubectl create secret generic llm-d-hf-token \
     --from-literal="HF_TOKEN=${HF_TOKEN}" \
     --namespace "${NAMESPACE}" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
<!-- llm-d-cicd:skip end -->

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

Deploy the llm-d Router in **Standalone Mode** overlaying router custom configurations:

```bash
# Run from the root of the llm-d repo
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/router/values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps:

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../../docs/infrastructure/gateway).
2. _Deploy the llm-d router and an HTTPRoute_ that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART}  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/router/values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlay:

```bash
export INFRA_PROVIDER=gke # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
```

---

## Verification

### 1. Retrieve the Proxy Endpoint IP

**Standalone Mode:**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode:</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send a Text-to-Speech Test Request

Open a debug container within the cluster namespace:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Send an OpenAI-compatible speech request. Unlike the JSON-based image endpoints, a successful response body is the binary audio itself, so write it to a file:

```bash
curl -s -X POST http://${IP}/v1/audio/speech \
    -H 'Content-Type: application/json' \
    -D headers.txt \
    -o speech.wav \
    -d '{
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "input": "vLLM is a fast and easy-to-use library for LLM inference and serving.",
        "voice": "vivian",
        "response_format": "wav"
    }'
```

Check that the response is a valid WAV file:

```bash
head -c 4 speech.wav; echo    # RIFF
```

vLLM-Omni also reports token usage for binary audio responses in headers (see the [vLLM-Omni speech API response format](https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/speech_api/#response-format)):

```bash
grep -i x-vllm-omni headers.txt
```

Expected header shape:

```text
x-vllm-omni-input-tokens: 18
x-vllm-omni-output-tokens: 52
x-vllm-omni-total-tokens: 70
```

> [!NOTE]
> The usage headers were added in vLLM-Omni v0.28.0. With the current default image (v0.26.0) the WAV file is returned but the `x-vllm-omni-*` headers are absent; usage is still reported in the `speech.audio.done` event when using `stream_format: "sse"`.

### 3. Streaming Variants

vLLM-Omni supports two streaming formats through the router.

**SSE stream** (`stream_format: "sse"`) returns `speech.audio.delta` events with base64 audio chunks and a final `speech.audio.done` event carrying usage:

```bash
curl -s -N -X POST http://${IP}/v1/audio/speech \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "input": "Hello from llm-d.",
        "voice": "vivian",
        "stream_format": "sse"
    }' | head -c 600
```

**Raw audio stream** (`stream_format: "audio"`) returns binary PCM chunks as they are generated, giving time-to-first-byte in the tens of milliseconds instead of seconds:

```bash
curl -s -N -X POST http://${IP}/v1/audio/speech \
    -H 'Content-Type: application/json' \
    -o speech-stream.pcm \
    -d '{
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "input": "Hello from llm-d.",
        "voice": "vivian",
        "stream_format": "audio"
    }'
```

---

## Cleanup

To tear down and clean up all deployed resources:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
```

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
