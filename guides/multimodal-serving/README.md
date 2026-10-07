# Multimodal Model Serving

## Overview

This guide serves a **multimodal model**, one that takes images, video, or audio alongside text and answers in text, behind the llm-d Router. The default deployment serves [`Qwen/Qwen3-VL-32B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct) with vLLM in one of three topologies:

- **Aggregated**: every model server pod encodes the media, prefills, and decodes. The router routes on prefix-cache affinity that covers images as well as text, and on token load.
- **E/PD**: dedicated **Encode** pods run the vision encoder, and Prefill/Decode pods consume the embeddings they produce.
- **E/P/D**: dedicated Encode, Prefill, and Decode pods, which adds an Encode stage to [P/D disaggregation](../pd-disaggregation/README.md).

> [!WARNING]
> Encode disaggregation (E/PD, E/P/D) is **experimental** and under active development in vLLM, SGLang, and the llm-d Router. The manifests may change in upcoming releases.

### Why multimodal routing

Multimodal requests break the assumptions that make round-robin balancing work for ordinary HTTP traffic, even more than text LLM requests do:

- **Context inflation**: a single high-resolution image, audio clip, or video adds thousands of tokens to the prompt.
- **Heavy prefill**: running the vision or audio encoder and prefilling those tokens is expensive, so sending a repeated image to a pod that has not seen it wastes accelerator time.

The llm-d Router extends text prefix-cache scheduling to media: it hashes each image along with the text around it, estimates how many tokens it costs, and sends the request to the pod most likely to hold the matching encoder output and KV blocks, weighed against the load on each pod.

### Architecture

```text
Aggregated:  Client → Proxy → Router (EPP) → model server pod (encode + prefill + decode)

E/PD:        Client → Proxy → Router (EPP) → PD pod's routing sidecar
                                               ├─ Encode pod     (media only; embeddings over the EC Connector)
                                               └─ PD pod         (prefill + decode)

E/P/D:       Client → Proxy → Router (EPP) → Decode pod's routing sidecar
                                               ├─ Encode pod     (media only; embeddings over the EC Connector)
                                               ├─ Prefill pod    (reads the embeddings, prefills; KV cache over NIXL)
                                               └─ Decode pod     (decode only)
```

In the aggregated topology every pod runs the whole model. When a request arrives with an image, the same pod converts it into visual embeddings with the model's Vision Transformer (ViT), prefills the embeddings and text tokens, and decodes the answer. The router's `prefix-cache-affinity-filter` picks the pods most likely to have the prompt, image included, in cache, and the `token-load-scorer` picks among them by queued prefill tokens.

In the Encode-disaggregated topologies the router's `disagg-profile-handler` selects a PD (or Decode) pod and, when the request carries media, an Encode pod. The routing sidecar sends the media to the Encode pod, and vLLM's ECCPU Connector moves the resulting embeddings to the consumer (the PD pod in E/PD, the Prefill pod in E/P/D) over NIXL, so the consumer does not run the encoder. Text-only requests skip the Encode stage. Several Encode pods can process the media items of one request in parallel.
The request flows, the EC Connector, and the vLLM version it needs are described in the [E-Disaggregation scenario](./e-disaggregation/README.md#vllm-architecture).

### Choosing a topology

| Dimension | Aggregated | E/PD and E/P/D |
| --- | --- | --- |
| **Worker roles** | Homogeneous: every pod runs Encode + Prefill + Decode | Dedicated Encode pods, plus PD pods or separate P and D pods |
| **Where media is encoded** | On the pod the router picks | On a dedicated Encode pod |
| **Transfer overhead** | None: everything stays in the pod's accelerator memory | Embeddings (and, in E/P/D, KV cache) cross the network |
| **Several media items in one request** | Encoded one after another on one pod | Encoded in parallel across Encode pods |
| **Scaling** | Encode capacity scales with text generation | Encode pods scale, and can use different hardware, independently |
| **Deployment complexity** | Low: one Deployment and a simple router configuration | High: several tiers, transfer channels, and Encode selection |

Choose **aggregated** serving when media inputs are small (low-resolution images), the model is small, you have a good prefix-cache hit rate (which already avoids repeated encoding), or you do not want to run multi-tier networking (NIXL/ZMQ) between pods.

Choose **E/PD** when requests often carry large or many media items (document parsing with dozens of images, high-definition video, long audio), the vision encoder is heavy enough to stall decoding on the pods that run it, or you want to scale encoding separately.
Choose **E/P/D** when, in addition, prefill and decode need separate scaling or parallelism: see [P/D Best Practices](../pd-disaggregation/README.md#pd-best-practices), and note that [Known NIXL Connector Issues and Limitations](../../docs/operations/disaggregation/vllm.md#known-nixl-connector-issues-and-limitations) apply to its P/D stage.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-VL-32B-Instruct` | ✅ validated | H200 reference · aggregated 8 replicas × TP=2 (16 GPUs), E/PD 2 Encode + 8 PD, E/P/D 2 Encode + 4 Prefill + 4 Decode (TP=2) · `INFRA_PROVIDER` `base`, `gke` (+ `coreweave` for E/PD and E/P/D) · E/P/D is not run nightly |
| Intel XPU | `xpu` | `Qwen/Qwen3-VL-32B-Instruct` | 🟡 community | Arc Pro B60 · aggregated only · 2 replicas × 2 GPUs through DRA · `INFRA_PROVIDER=base` |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3-VL-32B-Instruct` | ✅ validated | GKE only (`INFRA_PROVIDER=gke`) · aggregated only · 8 replicas × 4 chips (`2x2x1`, TP=4) · needs the `medium` PriorityClass |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

The aggregated topology runs on every accelerator above; E/PD and E/P/D run on NVIDIA GPUs only. The overlay each combination deploys:

| `TOPOLOGY` | `ACCELERATOR_TYPE` | `INFRA_PROVIDER` | Overlay |
| --- | --- | --- | --- |
| `aggregation` | `gpu` | `base`, `gke` | [`aggregation/modelserver/gpu/vllm/`](./aggregation/modelserver/gpu/vllm/) |
| `aggregation` | `xpu` | `base` | [`aggregation/modelserver/xpu/vllm/base/`](./aggregation/modelserver/xpu/vllm/base/) |
| `aggregation` | `tpu/v7` | `gke` | [`aggregation/modelserver/tpu/v7/vllm/gke/`](./aggregation/modelserver/tpu/v7/vllm/gke/) |
| `e-pd` | `gpu` | `base`, `gke`, `coreweave` | [`e-disaggregation/modelserver/gpu/vllm/e-pd/`](./e-disaggregation/modelserver/gpu/vllm/e-pd/) |
| `e-p-d` | `gpu` | `base`, `gke`, `coreweave` | [`e-disaggregation/modelserver/gpu/vllm/e-p-d/`](./e-disaggregation/modelserver/gpu/vllm/e-p-d/) |

The replica counts are sized for the reference hardware in the Notes column (16 GPUs for aggregated serving on NVIDIA GPUs, 20 for E/PD and E/P/D). To try the guide on a smaller cluster, lower `replicas` in the overlay's Deployments, keeping at least one pod per role.

SGLang serves the E/PD topology with Intel XPU Encode workers and an NVIDIA GPU PD worker: see the [SGLang XPU Encode + GPU PD profile](./e-disaggregation/profiles/sglang-xpu-encode-gpu-pd.md), which has its own variables and cluster requirements.

> [!NOTE]
> Intel XPU pods request their GPUs through Kubernetes Dynamic Resource Allocation (DRA, `resource.k8s.io/v1` `ResourceClaimTemplate`): the cluster needs the Intel DRA driver and the `gpu.intel.com` `DeviceClass`. TPU pods schedule onto a `tpu7x` node pool with a `2x2x1` topology and request all 4 chips of the node (GKE requires it), with `--tensor-parallel-size=4` to use them; change the `nodeSelector`, chip count, and tensor parallelism together if your slice differs. They also set `priorityClassName: medium`, which this guide does not create: create it or drop the field.

### Model-specific notes

The router estimates how many tokens each image or video costs (see [Token estimation](#token-estimation)), and the estimate must match the model you deploy. The shipped router values are calibrated for Qwen3-VL.
The `aggregation/modelserver/tpu/v7/vllm/gemma4/` overlay serves `google/gemma-4-31B-it` instead: Gemma 4 allocates a fixed token budget per image, so deploying it requires swapping in the commented-out Gemma 4 `estimate:` block in [`aggregation/router/aggregation.values.yaml`](./aggregation/router/aggregation.values.yaml) and setting `MODEL=google/gemma-4-31B-it`. Detailed tuning for Gemma 4 belongs with the model-specific guides.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (see the Notes column above; NVIDIA GPUs with 141 GB of HBM, e.g. H200, for the reference sizing).

- For E/P/D, the same KV-transfer networking as [P/D disaggregation](../pd-disaggregation/README.md#prerequisites). The GKE overlays do not configure RDMA yet, so embedding and KV-cache transfers use TCP: for the DRA and DRANet (RoCE) setup, see [Cluster Pre-provisioning](../pd-disaggregation/README.md#gke-cluster-pre-provisioning-with-dra--rdmaroce) and the [`gke-rdma` component](../recipes/modelserver/components/gke-rdma).

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
export GUIDE_NAME=multimodal-serving
export NAMESPACE=llm-d-multimodal-serving
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export TOPOLOGY=aggregation # options: aggregation, e-pd, e-p-d
export ACCELERATOR_TYPE=gpu # options: gpu, xpu, tpu/v7
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=base # options: base, gke, coreweave
export MODEL=Qwen/Qwen3-VL-32B-Instruct # the model the overlays serve (table above)
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

### 1. Select the topology

**Set the paths for the topology you chose** with `TOPOLOGY` (used by every step below):

<!-- tabs:start group=topology -->
<details open>
<summary><b>Aggregated</b></summary>

<!-- guide:deploy.topology.aggregation start -->
```bash
# only when TOPOLOGY=aggregation:
export TOPOLOGY_DIR="${REPO_ROOT}/guides/${GUIDE_NAME}/aggregation"
export ROUTER_VALUES="${TOPOLOGY_DIR}/router/aggregation.values.yaml"
export MODEL_SERVER_PATH="${TOPOLOGY_DIR}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}"
export MONITORING_COMPONENT=monitoring
# The pods the InferencePool selects (matchLabels in the router values)
export POD_SELECTOR=llm-d.ai/guide=aggregation
```
<!-- guide:deploy.topology.aggregation end -->

</details>
<details>
<summary><b>E/PD and E/P/D</b></summary>

<!-- guide:deploy.topology.disaggregation start -->
```bash
# only when TOPOLOGY=e-pd or e-p-d:
export TOPOLOGY_DIR="${REPO_ROOT}/guides/${GUIDE_NAME}/e-disaggregation"
export ROUTER_VALUES="${TOPOLOGY_DIR}/router/${MODEL_SERVER}/${TOPOLOGY}-disaggregation.values.yaml"
export MODEL_SERVER_PATH="${TOPOLOGY_DIR}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TOPOLOGY}/${INFRA_PROVIDER}"
export MONITORING_COMPONENT=monitoring-pd
# The pods the InferencePool selects (matchLabels in the router values)
export POD_SELECTOR=llm-d.ai/guide=e-disaggregation
```
<!-- guide:deploy.topology.disaggregation end -->

</details>
<!-- tabs:end -->

The model server pods keep the labels the nightly E2E scenarios use (`llm-d.ai/guide=aggregation` or `llm-d.ai/guide=e-disaggregation`): the router values select them by that label, and `POD_SELECTOR` is used in [Verification](#verification).

### 2. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below):

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files (ROUTER_VALUES is set by the topology step above)
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
```
<!-- guide:deploy.router_values end -->

The router values configure scheduling for each topology:

- [`aggregation.values.yaml`](./aggregation/router/aggregation.values.yaml): the `token-producer` estimates the tokens of each image and video, the `prefix-cache-affinity-filter` keeps requests on the pods that hold their prefix, and the `token-load-scorer` picks among them.
The filter is tuned for Qwen3-VL-32B-Instruct on H200 (TP=2): `peakPrefillThroughput: 15751` was measured with the [router calibration recipe](../recipes/router/calibration), `affinityThreshold: 0.6` sits below the cacheable fraction of typical multimodal prompts (the 0.8 default would never engage), and `maxTTFTPenaltyMs: 36000` (twice the default) compensates for vision-encoder time that token counts do not capture. Recalibrate them for other models and hardware.
- [`e-pd-disaggregation.values.yaml`](./e-disaggregation/router/vllm/e-pd-disaggregation.values.yaml) and [`e-p-d-disaggregation.values.yaml`](./e-disaggregation/router/vllm/e-p-d-disaggregation.values.yaml): the `disagg-profile-handler` with the `always-disagg-multimodal-decider` sends every request with media through an Encode pod, chosen by the `encode-filter`, and picks the PD (or Prefill and Decode) pods on prefix-cache affinity, token load, and queue depth.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is used by the verification steps to find the router service. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

### 3. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** selected in step 1:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${MODEL_SERVER_PATH}
```
<!-- guide:deploy.modelserver end -->

### 4. Enable Monitoring (optional)

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites) and `MONITORING=true` when deploying the router):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/${MONITORING_COMPONENT}
```
<!-- guide:deploy.monitoring end -->

## Token estimation

The router's prefix-cache and load scoring work in tokens, but a media item has no text length to read, so the `token-producer` estimates the tokens each one adds to the prompt and folds each image into the prefix hashes as a block weighted by that estimate. The estimate must match how the served model tokenizes media.

**Asset metadata.** For an image embedded inline in the request (as in this guide's tests), the router reads its width and height from the image itself. For an image given as a URL, it does not fetch the content and falls back to the configured `defaultResolution`. The router cannot inspect video, so clients should send video metadata in request headers; a missing header falls back to the configured default:

- `x-llm-d-video-fps`: the video's frames per second.
- `x-llm-d-video-duration-seconds`: the video's length in seconds.
- `x-llm-d-video-resolution`: the frame resolution as `WIDTHxHEIGHT` (for example `1280x720`).

**Dimension-based estimate** (`mode: dynamic`, Qwen-VL models). An image costs `width × height / factor` tokens, where `factor` is the area one visual token covers: `784` (28 × 28) for Qwen2.5-VL, `1024` (32 × 32) for Qwen3-VL, which this guide ships. A video costs `tokensPerFrame × frames`, where `tokensPerFrame` uses the same formula and, with the `sampled` frame strategy Qwen3-VL uses, `frames = clamp(duration × sampleFPS, minFrames, maxFrames) / temporalPatchSize`, capped at `maxVideoTokens` in total.
See the `estimate:` block in [`aggregation.values.yaml`](./aggregation/router/aggregation.values.yaml).

**Fixed estimate** (`mode: static`, for example Gemma 4). Every image costs a fixed, configured number of tokens matching the model's supported budgets (70, 140, 280, 560, or 1120 for Gemma 4), and a video costs a fixed `numTokensPerFrame` times the frames taken every `frameStride` source frames (`strided` strategy), clamped to `[minFrames, maxFrames]`. See [Model-specific notes](#model-specific-notes).

**Precise prefix-cache routing.** Instead of estimating, the router can tokenize the input and subscribe to the KV-cache events of the model servers, keeping an index of which blocks each pod holds. Media items become block keys derived from the asset hash and size, so they are matched like text. See the [KV-Cache Indexer](../../docs/architecture/advanced/kv-management/kv-indexer.md) and [Precise Prefix Cache Aware Routing](../precise-prefix-cache-routing/README.md).

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a multimodal request:**

<!-- guide:verify.tests.request start -->
```bash
# An image (a 64x64 PNG embedded in the request, so the pods need no
# outbound network access) and a question about it
kubectl run mm-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'IMG="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsKZeS0BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEDgsqnc8OJg6Ln3AAAAAElFTkSuQmCC"
    curl -sS -X POST "http://${IP}/v1/chat/completions" -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": [{\"type\": \"image_url\", \"image_url\": {\"url\": \"${IMG}\"}}, {\"type\": \"text\", \"text\": \"What color is this image?\"}]}], \"max_tokens\": 64}" \
      | jq -r ".choices[0].message.content"'
```
<!-- guide:verify.tests.request end -->

The model answers with the color of the image (red). A text answer alone does not show which pod encoded the image: the next step does.

### 3. Verify multimodal routing

Send the same long prompt with the same image twice, and a text-only request, then read the counters of the model server pods.

**Send the requests:**

<!-- guide:verify.tests.mm_requests start -->
```bash
# The same ~700-token prompt and image twice, then a text-only
# request: the router hashes text and image together, and the
# Encode stage (E/PD, E/P/D) only runs for requests with media
kubectl run mm-cache-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 40); do printf "The router hashes the text and the image of a multimodal request to find a warm pod. "; done)
    IMG="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsKZeS0BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEDgsqnc8OJg6Ln3AAAAAElFTkSuQmCC"
    for i in 1 2; do
      curl -sS -o /dev/null -w "image request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": [{\"type\": \"text\", \"text\": \"${P} What color is this image?\"}, {\"type\": \"image_url\", \"image_url\": {\"url\": \"${IMG}\"}}]}], \"max_tokens\": 16}"
    done
    curl -sS -o /dev/null -w "text-only request: HTTP %{http_code}\n" -X POST "http://${IP}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"How are you today?\"}], \"max_tokens\": 16}"'
```
<!-- guide:verify.tests.mm_requests end -->

**Read the request, prefix-cache, and multimodal-cache counters** of the model server pods:

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Request, prefix-cache, and multimodal-cache counters of every pod
# the router selects, read through the Kubernetes API server proxy
# (no port-forward needed). Each pod is read on its vLLM port: 8000,
# or 8200 for E/PD and E/P/D decode pods (behind the routing sidecar).
for pod in $(kubectl get pods -n ${NAMESPACE} -l ${POD_SELECTOR} -o jsonpath='{.items[*].metadata.name}'); do
  port=$(kubectl get pod ${pod} -n ${NAMESPACE} -o jsonpath='{.spec.containers[?(@.name=="modelserver")].ports[?(@.name=="modelserver")].containerPort}')
  echo "== ${pod} ($(kubectl get pod ${pod} -n ${NAMESPACE} -o jsonpath='{.metadata.labels.llm-d\.ai/role}'))"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:${port}/proxy/metrics" \
    | grep -E '^vllm:(request_success_total|prefix_cache_hits_total|mm_cache_(queries|hits)_total)' || true
done
```
<!-- guide:verify.tests.pod_metrics end -->

What to expect:

<!-- tabs:start group=topology -->
<details open>
<summary><b>Aggregated</b></summary>

The two image requests land on the same pod, whose `vllm:request_success_total` grows by 2 (the text-only request can go anywhere). On that pod, `vllm:prefix_cache_hits_total` grows by roughly the prompt length with the second request, and `vllm:mm_cache_hits_total` by 1: the router matched the text and the image to the pod that already processed them. If the two requests landed on different pods, the router did not apply prefix-cache affinity: check the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`).

</details>
<details>
<summary><b>E/PD and E/P/D</b></summary>

The Encode pods' `vllm:request_success_total` grows with the two image requests and not with the text-only one, and the PD pods (E/PD) or the Prefill and Decode pods (E/P/D) count all three. If the Encode pods count no requests, the router is not selecting an Encode pod: check that the pods carry the `llm-d.ai/role` labels and the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`).
An Encode pod that counts the requests does not prove that the consumer used its output, since a consumer that receives no embeddings encodes the media itself: to confirm the transfer, follow [Confirm the EC Transfer](./e-disaggregation/README.md#3-confirm-the-ec-transfer-vllm-profiles).

</details>
<!-- tabs:end -->

**(Optional) Read the router's prefix-cache and disaggregation metrics** (`MONITORING=true` only: without the monitoring values the router's metrics endpoint requires authentication):

<!-- guide:verify.tests.router_metrics start -->
```bash
# only when MONITORING=true:
# Prefix-cache matches and disaggregation decisions taken by the
# router, read through the API server service proxy
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_(prefix_indexer_hit_ratio_(sum|count)|disagg_decision_total)' || true
```
<!-- guide:verify.tests.router_metrics end -->

`llm_d_epp_prefix_indexer_hit_ratio` (sum over count) is the average fraction of each prompt the router found cached, and `llm_d_epp_disagg_decision_total` (E/PD and E/P/D) counts the router's disaggregation decisions by `decision_type`.

Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${MODEL_SERVER_PATH}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/${MONITORING_COMPONENT} --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->

## References

- [EPP Architecture](../../docs/architecture/core/router/epp/README.md)
- [llm-d Router Disaggregation Docs](https://github.com/llm-d/llm-d-router/blob/main/docs/disaggregation.md)
- [vLLM: Disaggregated Encoder](https://docs.vllm.ai/en/latest/features/disagg_encoder/)
- [vLLM: Encoder Disaggregation for Scalable Multimodal Model Serving](https://vllm.ai/blog/vllm-epd)
- [Serve Omni Models](../omni-serving/README.md), for models that also produce audio or images
