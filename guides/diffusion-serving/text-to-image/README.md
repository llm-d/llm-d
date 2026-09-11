# [Experimental] Diffusion Text-to-Image Guide

This guide deploys an image diffusion model behind the llm-d router and serves it over the OpenAI-compatible `POST /v1/images/generations` endpoint.

Currently, this guide routes on load: the EPP tracks how many requests it has dispatched to each endpoint and not yet seen complete, and prefers the least busy one.

---

## Default Configuration

| Parameter        | Value                                                     |
| ---------------- | --------------------------------------------------------- |
| Default Model    | [Qwen/Qwen-Image](https://huggingface.co/Qwen/Qwen-Image)  |
| Replicas         | 3                                                          |
| GPUs per replica | 1                                                          |
| Total GPUs       | 3                                                          |
| Accelerator      | NVIDIA H100 80GB                                           |

Qwen-Image is a ~20B MMDiT plus a ~8B text encoder, about 57 GB in bf16. It fits one 80 GB H100 at full precision.

### Supported Hardware Backends

| Backend    | Directory                                     | Model                       | Notes                                                            |
| ---------- | --------------------------------------------- | --------------------------- | ---------------------------------------------------------------- |
| NVIDIA GPU | `modelserver/gpu/vllmomni/${INFRA_PROVIDER}/` | `Qwen/Qwen-Image` | Default configuration (`INFRA_PROVIDER` options: `base`, `gke`)   |
| NVIDIA GPU | `modelserver/gpu/sglang/${INFRA_PROVIDER}/`   | `Qwen/Qwen-Image` | `sglang serve` dispatches to the `sglang.multimodal_gen` backend, which publishes no metrics |

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
   export GUIDE_NAME="text-to-image"
   export NAMESPACE=llm-d-diffusion-t2i
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

6. [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models. Qwen-Image is Apache-2.0, so the token is optional for the default model.
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

### 2. Send a Text-to-Image Test Request

Open a debug container within the cluster namespace:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Send an OpenAI-compatible image generation request. `"response_format": "b64_json"` requests the image inline, which is the reliable form behind the router. (The `jq` filter truncates the base64 payload so the response stays readable):


```bash
curl -X POST http://${IP}/v1/images/generations \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen-Image",
        "prompt": "a red apple on a wooden table",
        "size": "512x512",
        "n": 1,
        "response_format": "b64_json"
    }' | jq '.data[0].b64_json |= if . then .[0:24] + "..." else . end'
```

or you can write the result to a file:

```bash
curl -s -X POST http://${IP}/v1/images/generations \
    -H 'Content-Type: application/json' \
    -d '{"model":"Qwen/Qwen-Image","prompt":"a red apple on a wooden table","size":"512x512","n":1,"response_format":"b64_json"}' \
    | jq -r '.data[0].b64_json' | base64 -d > out.png
```

Different engines answer with different slightly different format:

**vLLM-Omni**:

```json
{
  "created": 1787080535,
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

**SGLang**, with timing fields:

```json
{
  "id": "27c8e232-d859-4f65-a667-5693b5a2bf17",
  "created": 1787104451,
  "data": [
    {
      "b64_json": "/9j/4AAQSkZJRgABAQEAYABg...",
      "url": null,
      "revised_prompt": "a red apple on a wooden table",
      "file_path": "/sgl-workspace/sglang/outputs/27c8e232-....jpg"
    }
  ],
  "peak_memory_mb": 43304.0,
  "inference_time_s": 9.88
}
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
