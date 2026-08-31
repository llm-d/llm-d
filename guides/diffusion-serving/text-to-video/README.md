# Diffusion Text-to-Video Guide

This guide deploys a video diffusion model behind the llm-d router and serves it over the OpenAI-compatible `/v1/videos` endpoint family on vLLM-Omni.

Two properties separate this guide from the text-to-image guide:

* **The API is asynchronous.** `POST /v1/videos` returns a job record immediately; the client polls `GET /v1/videos/{id}` and downloads the result from `GET /v1/videos/{id}/content`. Job state lives only in the serving pod, which constrains scaling. See [Scaling](#scaling).
* **The body is a multipart form, not JSON.** `POST /v1/videos` carries `multipart/form-data` even for pure text-to-video, because the endpoint also accepts optional reference uploads. The router configuration pins a custom EPP image (`videos-v1`) whose `openai-parser` claims `/v1/videos` and reads the form fields directly. See [Parsing](#parsing).

---

## Default Configuration

| Parameter        | Value                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------- |
| Default Model    | [Wan-AI/Wan2.1-T2V-1.3B-Diffusers](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers)   |
| Replicas         | 1 (see [Scaling](#scaling))                                                                   |
| GPUs per replica | 1                                                                                             |
| Total GPUs       | 1                                                                                             |
| Accelerator      | NVIDIA H100 80GB                                                                              |
| EPP image        | `us-central1-docker.pkg.dev/bobzetian-gke-dev/bobinference/llm-d-router-endpoint-picker:videos-v1` |

Wan2.1-T2V-1.3B is the smallest text-to-video model vLLM-Omni supports (`WanPipeline`): a 1.3B DiT plus a UMT5-XXL text encoder, generating 480p clips on a single GPU. It is Apache-2.0, so a HuggingFace token is optional.

### Supported Hardware Backends

| Backend    | Directory                                     | Model                              | Notes                                                          |
| ---------- | --------------------------------------------- | ---------------------------------- | --------------------------------------------------------------- |
| NVIDIA GPU | `modelserver/gpu/vllmomni/${INFRA_PROVIDER}/` | `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | Default configuration (`INFRA_PROVIDER` options: `base`, `gke`) |

SGLang is not included: it does not serve a `/v1/videos` endpoint.

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
   export GUIDE_NAME="text-to-video"
   export NAMESPACE=llm-d-diffusion-t2v
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

6. [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models. Wan2.1 is Apache-2.0, so the token is optional for the default model.
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
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/diffusion-serving/${GUIDE_NAME}/modelserver/gpu/${ENGINE}/${INFRA_PROVIDER}/
```

Wait for the rollout; the first start downloads roughly 20 GB of weights:

```bash
kubectl rollout status -n ${NAMESPACE} deploy -l llm-d.ai/guide=diffusion-serving-t2v --timeout=30m
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

### 2. Create a Video Generation Job

Open a debug container within the cluster namespace:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Create the job as a multipart form. Wan2.1 requires `num_frames` of the form 4k+1; 33 frames at 16 fps is about a 2 second clip, and 25 steps keeps the run short:

```bash
video_id=$(curl -s -X POST http://${IP}/v1/videos \
    -F "model=Wan-AI/Wan2.1-T2V-1.3B-Diffusers" \
    -F "prompt=a red panda walking through fresh snow" \
    -F "size=832x480" \
    -F "num_frames=33" \
    -F "fps=16" \
    -F "num_inference_steps=25" | jq -r '.id')
echo ${video_id}
```

The server answers immediately with a queued job record:

```json
{
  "model": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
  "prompt": "a red panda walking through fresh snow",
  "id": "video_gen_5c1f6b0f2f0a4e3f9d1b2c3d4e5f6a7b",
  "object": "video",
  "status": "queued",
  "size": "832x480",
  "progress": 0,
  "seconds": "4",
  "quality": "default",
  "completed_at": null,
  "created_at": 1787200000
}
```

### 3. Poll Until Completed

```bash
curl -s http://${IP}/v1/videos/${video_id} | jq '{id, status, progress}'
```

Repeat until `status` is `completed` (it passes through `in_progress`). A request without a `prompt` field, or without a `multipart/form-data` content type, is rejected at the EPP before reaching a backend.

### 4. Download the Video

```bash
curl -sL http://${IP}/v1/videos/${video_id}/content -o out.mp4
```

The response is raw `video/mp4` bytes. Copy it out of the debug pod with `kubectl cp` if you want to view it locally.

### 5. Confirm the EPP Parsed the Multipart Form

The model name exists only as a `model` form field inside the multipart body - it appears in neither the URL nor any header - so seeing it in the EPP log proves the custom parsing path ran:

```bash
kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp | grep "Request handled" | tail -1
```

Expect `"incomingModelName":"Wan-AI/Wan2.1-T2V-1.3B-Diffusers"` alongside the selected endpoint. With the upstream EPP image the request would instead be rejected with `no parser registered matching path suffix for: /v1/videos`.

---

## Parsing

The upstream `openai-parser` unmarshals the request body as JSON, so it cannot read a multipart form, and the upstream EPP rejects `/v1/videos` with `no parser registered matching path suffix for: /v1/videos` (or requires a `passthrough-parser` fallback, which forwards the body blind).

The `videos-v1` EPP image pinned by this guide extends `openai-parser` to claim `/v1/videos` and parse the multipart form directly:

* The `model`, `prompt`, `size`, `seconds`, `width`, `height`, `num_frames`, `fps`, `num_inference_steps`, and `num_outputs_per_prompt` form fields are extracted. The request is scheduled with the real model name, so model-labelled EPP metrics and logs are populated.
* File parts (optional reference uploads) are skipped during parsing and the payload is kept as raw bytes, so the body forwards to the backend byte for byte. Because the payload stays raw, model rewrite does not apply.
* The follow-up `GET` and `DELETE` requests carry no body and never reach a parser; the EPP routes them to an arbitrary ready endpoint. With a single replica that endpoint is always the pod that owns the job.

---

## Scaling

This guide deploys exactly one model server replica, and that is a correctness requirement, not a tuning choice:

* vLLM-Omni keeps the job record in per-pod memory and writes the finished MP4 to the pod's local disk. A poll that lands on a different pod returns 404.
* The router has no job affinity: `GET /v1/videos/{id}` and `GET /v1/videos/{id}/content` are routed to an arbitrary ready endpoint.

With more than one replica, `POST` requests load-balance correctly but polls fail for any job owned by another pod. Scaling out needs either shared job/content storage in the backend or job-to-endpoint affinity in the router; neither exists today.

## Routing Signals

Diffusion offers none of the signals token-based routing uses: no KV cache, no prefix to hash, and vLLM-Omni publishes metrics under a `vllm_omni:` prefix the default extractor does not match. The configuration scores with `active-request-scorer`, which counts requests the EPP itself has dispatched and not yet seen complete. Because `POST /v1/videos` returns as soon as the job is queued, this count tracks job submission rather than generation time; with a single replica the scorer is effectively idle and matters only as a template for scaled-out setups. `dataLayer.injectDefaults` is set to `false` so the EPP does not scrape metrics no scorer reads.

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
