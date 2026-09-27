# [Experimental] Diffusion Image-to-Image Guide

This guide deploys an image editing model behind the llm-d router and serves it over the OpenAI-compatible `POST /v1/images/edits` endpoint, which carries `multipart/form-data` because the OpenAI API accepts image uploads on it.

Currently, this guide routes on load: the EPP tracks how many requests it has dispatched to each endpoint and not yet seen complete, and prefers the least busy one.

---

## Default Configuration

| Parameter        | Value                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------- |
| Default Model    | [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)          |
| Replicas         | 3                                                                                       |
| GPUs per replica | 1                                                                                       |
| Total GPUs       | 3                                                                                       |
| Accelerator      | NVIDIA H100 80GB                                                                        |

An edit-capable checkpoint is required. `Qwen/Qwen-Image` reports task type `T2I` and rejects edits with `input_reference is not supported for T2I models`. `Qwen/Qwen-Image-Edit-2511` reports `I2I` and is the reverse, rejecting `/v1/images/generations` with `Served model with task type 'I2I' requires an 'image_path' input`.

### Supported Hardware Backends

| Backend    | Directory                                     | Model                       | Notes                                                            |
| ---------- | --------------------------------------------- | --------------------------- | ---------------------------------------------------------------- |
| NVIDIA GPU | `modelserver/gpu/vllmomni/${INFRA_PROVIDER}/` | `Qwen/Qwen-Image-Edit-2511` | Default configuration (`INFRA_PROVIDER` options: `base`, `gke`)   |
| NVIDIA GPU | `modelserver/gpu/sglang/${INFRA_PROVIDER}/`   | `Qwen/Qwen-Image-Edit-2511` | `sglang serve` dispatches to the `sglang.multimodal_gen` backend, which publishes no metrics |

Both engines run the same checkpoint, so they are comparable under one router configuration.

> [!NOTE]
> Deploy one engine at a time. Both overlays carry the same `llm-d.ai/guide` label, which is what the router selects on.

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
   export GUIDE_NAME="image-to-image"
   export NAMESPACE=llm-d-diffusion-i2i
   export ENGINE=vllmomni # vllmomni | sglang
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

6. [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models.
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

Apply the Kustomize overlay for your engine (defaulting to vLLM-Omni):

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
```

First start downloads roughly 57 GB of weights and compiles, so `startupProbe` allows up to an hour:

```bash
kubectl rollout status -n ${NAMESPACE} deploy -l llm-d.ai/guide=diffusion-serving-i2i --timeout=60m
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

### 2. Send an Image Edit Test Request

Open a debug container within the cluster namespace:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Fetch a sample image, then send it as a multipart form. Truncate the base64 payload so the response stays readable:

```bash
curl -s -o input.png https://picsum.photos/512/512

curl -X POST http://${IP}/v1/images/edits \
    -F "image=@input.png" \
    -F "prompt=convert this photo to a pencil sketch" \
    -F "model=Qwen/Qwen-Image-Edit-2511" \
    -F "size=512x512" \
    -F "n=1" | jq '.data[0].b64_json |= if . then .[0:24] + "..." else . end'
```

Different engines answer with slightly different formats:

**vLLM-Omni** answers with inline base64 on this endpoint:

```json
{
  "created": 1787080788,
  "data": [
    {
      "b64_json": "iVBORw0KGgoAAAANSUhEUg...",
      "url": null,
      "revised_prompt": null
    }
  ],
  "output_format": "png",
  "size": "512x512",
  "cot_output": null
}
```

**SGLang** answers with the same `data[0].b64_json` field, plus timing fields:

```json
{
  "id": "b7c1f0e2-3a4d-4c2e-9f11-0d5a7c8e2b34",
  "created": 1787113833,
  "data": [
    {
      "b64_json": "/9j/4AAQSkZJRgABAQEAYABg...",
      "url": null,
      "revised_prompt": "convert this photo to a pencil sketch",
      "file_path": "/sgl-workspace/sglang/outputs/b7c1f0e2-....jpg"
    }
  ],
  "peak_memory_mb": 48030.0,
  "inference_time_s": 14.66
}
```

Write the edited image to a file:

```bash
curl -s -X POST http://${IP}/v1/images/edits \
    -F "image=@input.png" \
    -F "prompt=convert this photo to a pencil sketch" \
    -F "model=Qwen/Qwen-Image-Edit-2511" \
    -F "size=512x512" \
    | jq -r '.data[0].b64_json' | base64 -d > edited.png
```

A 512x512 edit takes roughly 15 s on one H100 at about 48 GB peak. Neither engine returns a `usage` block, so the EPP records no token counts for image requests.

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
