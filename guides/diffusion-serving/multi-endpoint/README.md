# [Experimental] Multi-Endpoint Serving Guide

This guide deploys a model that serves **one or more modality endpoints** behind the llm-d router, using Gateway API exact-path HTTPRoutes to direct each endpoint to the model's InferencePool.

The other guides in this package ([text-to-speech](../text-to-speech/README.md), [text-to-image](../text-to-image/README.md), [image-to-image](../image-to-image/README.md)) each deploy a dedicated model for a single endpoint. This guide covers the **one model → multiple endpoints** pattern, where a single unified model generates multiple output types from one checkpoint.

| Model | Engine | Endpoints | Path matches to keep |
|---|---|---|---|
| **Qwen3-Omni** (default) | vLLM-Omni | `/v1/chat/completions`, `/v1/audio/speech`, `/v1/audio/transcriptions` | `/v1/audio/speech`, `/v1/audio/transcriptions` |
| **Qwen3-TTS** | vLLM-Omni | `/v1/audio/speech` | `/v1/audio/speech` |
| **FLUX / Diffusion** | vLLM-Omni | `/v1/images/generations` | `/v1/images/generations` |
| **Future omni model** | vLLM-Omni | All of the above | All path matches |

> [!WARNING]
> Multi-endpoint serving is **experimental**. The routing configuration is an early baseline, and the manifests may change in upcoming releases.

---

## Why vLLM-Omni?

Standard vLLM generates **text output only** — even when the model accepts multimodal input (images, audio, etc.), the response is always text. This covers most LLM use cases, including vision-language models like LLaVA or Qwen-VL.

For models whose output is a **non-text modality** — audio (text-to-speech, speech-to-speech), images (text-to-image), or a combination — the inference engine to use is **[vLLM-Omni](https://github.com/vllm-project/vllm-omni)**. vLLM-Omni extends vLLM with support for generating audio and image outputs, enabling endpoints like `/v1/audio/speech`, `/v1/audio/transcriptions`, and `/v1/images/generations`.

| Engine | Input | Output | Example models |
|---|---|---|---|
| **vLLM** | Text, images, audio | **Text only** | Llama, Qwen-VL, LLaVA |
| **vLLM-Omni** | Text, images, audio | **Text, audio, images** | Qwen3-Omni, Qwen3-TTS, FLUX |

This guide uses the `vllmomni` engine type (`ENGINE=vllmomni`) and references the pre-existing `gpu-vllm-omni` [image component](../../recipes/modelserver/components/images/gpu-vllm-omni/) which is maintained centrally in the recipes directory.

---

## Architecture

```
Client → Gateway → HTTPRoute (exact-path match) → model pool → model pods

  /v1/audio/speech         (Exact) → model-pool → model pods  (if model supports TTS)
  /v1/audio/transcriptions (Exact) → model-pool → model pods  (if model supports ASR)
  /v1/images/generations   (Exact) → model-pool → model pods  (if model supports image gen)
  /v1/chat/completions             → catch-all  → model-pool  (or text pool)
```

A single HTTPRoute with multiple exact-path match rules directs modality-specific endpoints to the model's InferencePool. You remove path matches for endpoints your model does not support. Shared endpoints like `/v1/chat/completions` fall through to the `PathPrefix: /` catch-all from the inference-gateway component.

To add a text-only pool (e.g. Llama) alongside a multi-endpoint model, deploy a second InferencePool + EPP and point the catch-all at the text pool. Exact-path routes take precedence per Gateway API specification, so modality-specific requests continue to reach the correct pool.

The EPP uses the standard config (`queue-scorer` + `max-score-picker`, no filters) — pod homogeneity within the pool eliminates the need for architecture-aware filtering.

---

## Default Configuration

| Parameter          | Value                                                          |
| ------------------ | -------------------------------------------------------------- |
| Default Model      | Qwen3-Omni (text + audio; swap for any vLLM-compatible model)  |
| Replicas           | 1                                                              |
| GPUs per replica   | Model-dependent (e.g. Qwen3-Omni requires 2)                  |
| Accelerator        | NVIDIA GPU                                                     |
| Endpoints served   | Depends on model — keep only the HTTPRoute path matches for endpoints the model supports |

---

## Prerequisites

1. Install the local client tooling using the [client setup guide](../../../helpers/client-setup/README.md).
2. Clone and check out the llm-d repository:

   ```bash
   export branch="main"
   git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   ```

3. Set up environment variables:

   ```bash
   source ${REPO_ROOT}/guides/env.sh
   export GUIDE_NAME="multi-endpoint-serving"
   export NAMESPACE=llm-d-multi-endpoint-serving
   export ENGINE=vllmomni
   export INFRA_PROVIDER=base  # base | gke
   ```

4. Install the Gateway API Inference Extension CRDs:

   ```bash
   kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
   ```

5. Create the namespace:

   ```bash
   kubectl create namespace ${NAMESPACE}
   ```

6. (Optional) Create a HuggingFace token secret if the model requires authentication:

   ```bash
   export HF_TOKEN=<your HuggingFace token>
   kubectl create secret generic llm-d-hf-token \
     --from-literal="HF_TOKEN=${HF_TOKEN}" \
     --namespace "${NAMESPACE}" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

Deploy the llm-d Router in **Standalone Mode** with the multi-endpoint router configuration:

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/router/values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary>Gateway Mode</summary>

To use a Kubernetes Gateway managed proxy:

1. Deploy a Kubernetes Gateway by following one of [the gateway guides](../../../docs/infrastructure/gateway).
2. Deploy the llm-d router and an HTTPRoute:

```bash
export PROVIDER_NAME=gke  # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/router/values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
</details>

### 2. Apply Multimodal HTTPRoutes

After deploying the router, apply the HTTPRoute for the endpoints your model supports. The `httproutes.yaml` includes path matches for all supported modalities — **edit the file to remove path matches for endpoints your model does not support** before applying:

| Path match | Endpoint | Use when model supports |
|---|---|---|
| `/v1/audio/speech` | Text-to-speech | TTS (e.g. Qwen3-Omni, Qwen3-TTS) |
| `/v1/audio/transcriptions` | Speech-to-text | ASR (e.g. Qwen3-Omni) |
| `/v1/images/generations` | Image generation | Image gen (e.g. FLUX, diffusion models) |

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/httproutes.yaml
```

### 3. Deploy the Model Server

Apply the Kustomize overlay for the omni model:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
```

---

## Verification

### 1. Retrieve the Proxy Endpoint IP

**Standalone Mode:**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary>Gateway Mode</summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
</details>

### 2. Open a Debug Container

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

### 3. Test Text Chat

> Applies to any model that supports `/v1/chat/completions`.

```bash
curl -s -X POST http://${IP}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "YOUR_OMNI_MODEL_NAME",
        "messages": [{"role": "user", "content": "Hello, what can you do?"}],
        "max_tokens": 128
    }' | jq .
```

> For the default deployment, replace `YOUR_OMNI_MODEL_NAME` with `Qwen/Qwen3-Omni`.

### 4. Test Text-to-Speech

> Applies to models that support `/v1/audio/speech` (e.g. Qwen3-Omni, Qwen3-TTS).

```bash
curl -s -X POST http://${IP}/v1/audio/speech \
    -H 'Content-Type: application/json' \
    -D headers.txt \
    -o speech.wav \
    -d '{
        "model": "YOUR_OMNI_MODEL_NAME",
        "input": "llm-d routes multimodal requests to omni model pods.",
        "voice": "alloy",
        "response_format": "wav"
    }'

# Verify WAV file
head -c 4 speech.wav; echo    # expect: RIFF
```

### 5. Test Image Generation

> Applies to models that support `/v1/images/generations` (e.g. FLUX, diffusion models). Skip if your model does not support image generation.

```bash
curl -s -X POST http://${IP}/v1/images/generations \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "YOUR_OMNI_MODEL_NAME",
        "prompt": "a red apple on a wooden table",
        "size": "512x512",
        "n": 1,
        "response_format": "b64_json"
    }' | jq '.data[0].b64_json |= if . then .[0:24] + "..." else . end'
```

### 6. Test Audio Transcription

> Applies to models that support `/v1/audio/transcriptions` (e.g. Qwen3-Omni). Skip if your model does not support ASR.

```bash
# Transcribe the speech.wav generated above
curl -s -X POST http://${IP}/v1/audio/transcriptions \
    -F file=@speech.wav \
    -F model="YOUR_OMNI_MODEL_NAME" | jq .
```

---

## Multi-Pool Deployment

To add a text-only model alongside a multi-endpoint model, deploy a second InferencePool in the same namespace and update the catch-all HTTPRoute:

```
/v1/audio/speech         (Exact)   → omni-pool   → omni pods   (if model supports TTS)
/v1/audio/transcriptions (Exact)   → omni-pool   → omni pods   (if model supports ASR)
/v1/images/generations   (Exact)   → omni-pool   → omni pods   (if model supports image gen)
/v1/chat/completions     (catch-all PathPrefix) → text-pool → text-only pods
```

Exact-path routes take precedence over `PathPrefix` per Gateway API specification, so modality-specific requests always reach the correct pool regardless of the catch-all target.

> [!NOTE]
> If two different models serve the same endpoint path (e.g. two TTS models both on `/v1/audio/speech`), path-based routing alone cannot distinguish between them. In that case, deploy each model in its own namespace with a separate Gateway endpoint, following the pattern in the single-endpoint guides ([text-to-speech](../text-to-speech/README.md), [text-to-image](../text-to-image/README.md), [image-to-image](../image-to-image/README.md)).

---

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/httproutes.yaml
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/multi-endpoint/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
kubectl delete namespace ${NAMESPACE}
```
