# Heterogeneous SGLang E/PD Disaggregation

## Overview

This experimental guide deploys `moonshotai/Kimi-VL-A3B-Instruct` with four
Intel XPU Encode workers and one NVIDIA GPU Prefill/Decode worker. It uses
SGLang for encoder dispatch and embedding transfer while the llm-d Router
selects the PD endpoint.

This is a different control and data path from the
[NVIDIA GPU vLLM E-disaggregation guide](../../README.md):

* The llm-d Router selects only PD workers.
* The SGLang PD worker assigns media items through static `--encoder-urls`.
* Encode workers return embeddings through SGLang `zmq_to_scheduler`.
* The E-to-PD path does not use the llm-d disaggregation sidecar, vLLM EC
  Connector, or NIXL.

Text-only requests skip the Encode workers.

## Reference Configuration

| Setting | Value |
| --- | --- |
| Model | `moonshotai/Kimi-VL-A3B-Instruct` |
| Topology | 4E1PD |
| Encode replicas | 4 |
| Encode accelerator | 1 Intel XPU per replica, allocated with DRA |
| Encode attention backend | `xpu_attn` |
| PD replicas | 1 |
| PD accelerator | 1 NVIDIA GPU |
| Encoder discovery | Static StatefulSet DNS names in `--encoder-urls` |
| Embedding transfer | SGLang `zmq_to_scheduler` |
| llm-d Router ownership | PD selection only |

The Encode replicas use a StatefulSet and headless Service with these stable
endpoints:

```text
encode-0.encode:8000
encode-1.encode:8000
encode-2.encode:8000
encode-3.encode:8000
```

The manifests do not select particular hosts, GPU UUIDs, PCI addresses, or
card indices. Kubernetes selects one NVIDIA GPU and four Intel XPUs from the
available cluster resources.

## When to Use This Configuration

This topology is intended for workloads where:

* requests contain several or high-resolution images;
* multimodal encoding materially contributes to time to first token;
* independent Encode scaling can remove a bottleneck; or
* Intel XPUs provide a useful cost or capacity tier for vision encoding.

Performance gains are model- and workload-dependent. Embedding transfer,
network hops, media count and resolution, request rate, and output length all
affect the result. Compare this deployment with an aggregated baseline using a
representative workload before production use.

## Prerequisites

1. Install the [client tools](../../../../../helpers/client-setup/README.md).
2. Clone and check out the llm-d repository:

   ```bash
   export branch="main" # branch, tag, or commit hash
   git clone https://github.com/llm-d/llm-d.git
   cd llm-d
   git checkout "${branch}"
   ```

3. Set the guide variables:

   ```bash
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   source "${REPO_ROOT}/guides/env.sh"
   export GUIDE_PATH="multimodal-serving/e-disaggregation"
   export RELEASE_NAME="heterogeneous-sglang-e-pd"
   export NAMESPACE="llm-d-sglang-heterogeneous-e-pd"
   export MODEL_NAME="moonshotai/Kimi-VL-A3B-Instruct"
   export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_PATH}/router/sglang-e-pd-disaggregation.values.yaml"
   export MODEL_SERVER_PATH="${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/heterogeneous/sglang/e-pd"
   ```

4. Install the Gateway API Inference Extension CRDs:

   ```bash
   kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
   ```

5. Create the namespace:

   ```bash
   kubectl create namespace "${NAMESPACE}" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

6. [Create the `llm-d-hf-token` secret](../../../../../helpers/hf-token.md)
   with a valid Hugging Face token:

   <!-- llm-d-cicd:skip start -->
   ```bash
   export HF_TOKEN=<your Hugging Face token>
   kubectl create secret generic llm-d-hf-token \
     --from-literal="HF_TOKEN=${HF_TOKEN}" \
     --namespace "${NAMESPACE}" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
   <!-- llm-d-cicd:skip end -->

### Cluster Requirements

The cluster must provide:

* at least one schedulable NVIDIA GPU exposed as `nvidia.com/gpu`;
* at least four schedulable Intel XPUs;
* Kubernetes Dynamic Resource Allocation using the `resource.k8s.io/v1` API;
* the
  [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes)
  and a `gpu.intel.com` DeviceClass; and
* pod-to-pod connectivity from the PD worker to Encode port 8000 and from the
  Encode workers to dynamic ZMQ receive ports on the PD pod.

Verify the accelerator resources before installation:

```bash
kubectl get deviceclass gpu.intel.com
kubectl get nodes \
  -o custom-columns='NAME:.metadata.name,NVIDIA_GPUS:.status.allocatable.nvidia\.com/gpu'
```

### Image Provenance

The overlay pins both images by digest:

| Role | Image |
| --- | --- |
| NVIDIA GPU PD | `docker.io/lmsysorg/sglang:v0.5.15.post1-cu130-runtime@sha256:247fd6bfcdeabe3e382da5c557537440148d08fd30f2b482f01fa09e242fe185` |
| Intel XPU Encode | `ghcr.io/xiaojun-zhang/llm-d-xpu-sglang:sglang-heterogeneous-e-pd@sha256:56ed840fe2890671a894fda14da1ead719ce18e5a94d3095a58ba5b54c41e55d` |

The official GPU image identifies SGLang source revision
`0b3bb0cbe31873994c9f989fddfe2f87ca839fdd`. The XPU image is built from
SGLang source revision `79dea0630ea909c15888984568eb9dbc8821f4dd`,
including the Kimi-VL 2-D image-grid fix, and `sgl-kernel-xpu` revision
`a246742797279015f51d135063ed00f879496896`. No runtime source patch or
ConfigMap is required.

> [!IMPORTANT]
> The fork-owned XPU package is private at the time this guide was authored.
> This review configuration therefore requires registry access. Before
> publication, the image must be made public or rebuilt and published under
> `ghcr.io/llm-d`. The source build is defined in
> `.github/workflows/build-image.yaml` with the `sglang-xpu` platform.
>
> The portable manifests and this exact image pair have passed static
> Kubernetes and Helm validation but still require an end-to-end GPU/XPU
> runtime test before publication.

## Installation

### 1. Deploy the llm-d Router

#### Standalone Mode

```bash
helm install "${RELEASE_NAME}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${ROUTER_VALUES}" \
  -n "${NAMESPACE}" \
  --version "${ROUTER_CHART_VERSION}"
```

<details>
<summary><b>Gateway Mode</b></summary>

Deploy a Kubernetes Gateway by following a
[gateway provider guide](../../../../../docs/infrastructure/gateway), then
install the Router and HTTPRoute:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install "${RELEASE_NAME}" \
  "${ROUTER_GATEWAY_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml" \
  -f "${ROUTER_VALUES}" \
  --set "provider.name=${PROVIDER_NAME}" \
  -n "${NAMESPACE}" \
  --version "${ROUTER_CHART_VERSION}"
```

</details>

### 2. Deploy the Model Servers

Confirm that the cluster has the required free accelerator capacity, then
apply the overlay:

```bash
kubectl apply -n "${NAMESPACE}" -k "${MODEL_SERVER_PATH}"
```

Kubernetes creates one `ResourceClaim` per Encode replica from the
`xpu-encoder` `ResourceClaimTemplate`. Each claim requests one device from
`gpu.intel.com`.

### 3. Enable Monitoring (Optional)

Install the [monitoring stack](../../../../../docs/operations/observability).
Add the following values file during Router installation:

```text
-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml
```

Deploy the PD model-server PodMonitor:

```bash
kubectl apply -n "${NAMESPACE}" \
  -k "${REPO_ROOT}/guides/recipes/modelserver/components/monitoring"
```

## Verification

### 1. Wait for the Workers

```bash
kubectl rollout status statefulset/encode \
  -n "${NAMESPACE}" --timeout=45m
kubectl rollout status deployment/decode \
  -n "${NAMESPACE}" --timeout=45m
kubectl get pods,resourceclaims -n "${NAMESPACE}"
```

The PD pod waits for all four Encode endpoints before starting SGLang.

### 2. Get the Proxy Address

For standalone mode:

```bash
export IP=$(kubectl get service "${RELEASE_NAME}-epp" \
  -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}')
```

For Gateway mode:

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway \
  -n "${NAMESPACE}" -o jsonpath='{.status.addresses[0].value}')
```

### 3. Send a Four-Image Request

Open a temporary shell in the cluster:

```bash
kubectl run curl-debug --rm -it \
  --namespace "${NAMESPACE}" \
  --image=cfmanteiga/alpine-bash-curl-jq \
  --env="IP=${IP}" \
  --env="MODEL_NAME=${MODEL_NAME}" \
  -- /bin/bash
```

The following request allows SGLang to dispatch one image to each Encode
worker:

```bash
curl -sS -X POST "http://${IP}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "'"${MODEL_NAME}"'",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "image_url",
            "image_url": {
              "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/assets/logo.png"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/examples/frontend_language/quick_start/images/cat.jpeg"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/examples/frontend_language/quick_start/images/dog.jpeg"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/examples/assets/example_image.png"
            }
          },
          {
            "type": "text",
            "text": "Briefly identify each of the four images in order."
          }
        ]
      }
    ],
    "max_tokens": 128,
    "temperature": 0
  }' | jq
```

Leave the debug shell and confirm that the PD worker dispatched all four
items:

```bash
kubectl logs deployment/decode -n "${NAMESPACE}" -c modelserver \
  | grep 'Dispatching 4 mm items to 4 encoder'
```

Confirm that all four Encode workers processed requests:

```bash
for ordinal in 0 1 2 3; do
  kubectl logs "encode-${ordinal}" -n "${NAMESPACE}" -c modelserver \
    --tail=100
done
```

### 4. Send a Text-Only Request

This request should complete without Encode dispatch:

```bash
curl -sS -X POST "http://${IP}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "'"${MODEL_NAME}"'",
    "messages": [
      {
        "role": "user",
        "content": "What is encode disaggregation?"
      }
    ],
    "max_tokens": 128,
    "temperature": 0
  }' | jq
```

## Limitations

* This is a fixed 4E1PD example for `moonshotai/Kimi-VL-A3B-Instruct`; it does
  not establish support or a performance benefit for other models or
  workloads.
* Encoder membership is static. Changing the StatefulSet replica count also
  requires updating the PD worker's `--encoder-urls`.
* The PD worker waits for all Encode workers at startup, but the llm-d Router
  does not track Encode health or load. If an Encode worker fails later,
  requests assigned to it can fail until its stable endpoint recovers.
* The E-to-PD path uses TCP-based HTTP and ZMQ communication. It does not
  configure RDMA or NIXL.
* The Encode workers expose metrics, but this guide does not yet include an
  Encode-specific PodMonitor.
* Hardware sizing, network policy, persistent model caching, autoscaling, and
  production availability policy remain deployment-specific.

## Cleanup

```bash
helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
kubectl delete -n "${NAMESPACE}" -k "${MODEL_SERVER_PATH}"
```

If Gateway mode was used, follow the selected provider guide to remove the
Gateway when it is no longer shared by other deployments.

## Architecture

```text
Client -> Proxy -> llm-d Router -> NVIDIA GPU PD worker
                                      |
                                      +-- HTTP encode work --> Intel XPU Encode workers
                                      |                         |
                                      +<---- ZMQ embeddings -----+
                                      |
                                      +-- prefill + decode --> Response
```

1. The llm-d Router selects only a ready PD endpoint using queue and KV-cache
   utilization signals.
2. The SGLang language-only PD worker parses the multimodal request and divides
   media items across its static `--encoder-urls`.
3. The XPU workers process assigned items with `xpu_attn`.
4. Each XPU worker sends embeddings to the PD scheduler through
   `zmq_to_scheduler`.
5. The GPU PD worker performs language-model prefill and decode and returns the
   response.

## References

* [llm-d Router architecture](https://github.com/llm-d/llm-d-router/blob/main/docs/architecture.md)
* [SGLang EPD disaggregation](https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/epd_disaggregation.md)
* [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes)
