# Multimodal Precise Affinity

## Overview

Repeated multimodal content is a routing opportunity: a single image expands to ~1500+ vision tokens through the render/encoder stage before prefill, so when the same image or video recurs across requests, routing the repeat to the pod that already encoded it skips that work, cutting TTFT and freeing encoder and prefill cycles.

This guide routes on precise per-pod **encoder-cache** state: the router records which pod last encoded each multimodal item and sends a request to the pod holding the largest fraction of its items, deterministic once the cache is warm. It is a routing capability over the encoder cache; which modalities the endpoint accepts is the model's concern, not the guide's (the reference model serves image and video).

Unlike [optimized-baseline](../optimized-baseline/), which estimates reuse from approximate hashes, this tracks exact multimodal item hashes per pod. The trade is a render sidecar on the EPP pod.

- **Precise multimodal-cache aware**: the `token-producer` calls the vLLM render endpoint (`/v1/chat/completions/render`) to resolve the multimodal prefix exactly. The [mm-embeddings-cache-producer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/dataproducer/multimodal) records, per pod, which item hashes were last routed there (a bounded LRU); the [mm-embeddings-cache-scorer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/mmcacheaffinity) scores candidates by resident-item fraction.
- **Load-aware**: the kv-cache-utilization and queue scorers balance against pod pressure so a warm pod is not overloaded.

## Default Configuration

| Parameter           | Value |
| ------------------- | ----- |
| Model               | [Qwen/Qwen3-VL-32B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct) (vision: image + video) |
| Replicas            | 4 |
| Tensor Parallelism  | 2 |
| GPUs per replica    | 2 |
| Total GPUs          | 8 |
| Render sidecar      | `vllm-render` on the EPP pod |

### Supported Hardware Backends

| Backend    | Directory               | Notes                 |
| ---------- | ----------------------- | --------------------- |
| NVIDIA GPU | `modelserver/gpu/vllm/` | Default configuration |

> [!NOTE]
> The `token-producer` `modelName` in [`router/precise-affinity.values.yaml`](router/precise-affinity.values.yaml) must match the model the overlay deploys, or render output will not match resident cache state.

## Prerequisites

- A cluster with at least 8 GPUs (4 replicas x 2 GPUs) of the chosen backend, plus CPU headroom for the `vllm-render` sidecar on the EPP pod.
- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.
- Checkout the llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

- Set the following environment variables:

  ```bash
  export GAIE_VERSION=v1.5.0
  export ROUTER_CHART_VERSION=v0
  export GUIDE_NAME="precise-affinity"
  export NAMESPACE="llm-d-multimodal-precise-affinity"
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

- Create the namespace:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

- Create the `llm-d-hf-token` secret in the namespace so the model server and render sidecar can pull the model. See [helpers/hf-token.md](../../../helpers/hf-token.md).

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

Deploy the llm-d Router in Standalone Mode. The release name `${GUIDE_NAME}` is mandatory; the inference pool selector matches a guide label that pairs with this release. The chart injects the `vllm-render` sidecar when `router.tokenizer.enabled: true` is set in the values file.

```bash
helm install ${GUIDE_NAME} \
  oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/multimodal-serving/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy instead of the standalone Envoy sidecar:

1. **Deploy a Kubernetes Gateway** named `llm-d-inference-gateway`. See [the gateway guides](../../prereq/gateways).
2. **Deploy the llm-d Router and HTTPRoute** via the `llm-d-router-gateway` chart:

```bash
export PROVIDER_NAME=istio # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
  oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
  -f ${REPO_ROOT}/guides/multimodal-serving/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  --set provider.name=${PROVIDER_NAME} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

```bash
export INFRA_PROVIDER=base # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/multimodal-serving/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
```

## Verification

### 1. Get the IP of the Proxy

#### Standalone Mode

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send a Multimodal Test Request

Open a debug container in the namespace, then send the same text + image request twice. The router resolves the prefix via the render sidecar on the first request, then routes the second to the pod that already holds it; confirm both land on the same pod via the `x-inference-pod` response header:

```bash
curl -i -X POST http://${IP}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-VL-32B-Instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    { "type": "text", "text": "What details are present in this photo?" },
                    { "type": "image_url", "image_url": { "url": "https://picsum.photos/id/237/640/360" } }
                ]
            }
        ]
    }'
```

On repeated content, `llm_d_router_epp_encoder_cache_hits_total{pod=...}` increments for the pod that served the cached item.

## How It Works

1. The `token-producer` calls the vLLM render endpoint (`/v1/chat/completions/render`) to resolve the request's multimodal prefix.
2. The `mm-embeddings-cache-producer` records, per pod, which multimodal item hashes were last routed there, in a bounded per-pod LRU. Its size is set by `cacheSizeInMBPerServer` in [`router/precise-affinity.values.yaml`](router/precise-affinity.values.yaml). The producer is self-contained: it tracks live pods via the framework pod list and updates the LRU from its own routing decisions; no endpoint-notification or data-layer wiring is required.
3. The `mm-embeddings-cache-scorer` returns the resident-item fraction per candidate; the `max-score-picker` routes to the highest-scoring pod.

> [!NOTE]
> The render call runs synchronously on the routing hot path on every request, served by a CPU-only `vllm-render` sidecar on the EPP pod. At high QPS this sidecar is the serialization point; size benchmarks to include TTFT under render load, not just per-pod cache-hit rate. A pod restart wipes its per-pod LRU, so affinity degrades to cold until the pod re-warms.

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/multimodal-serving/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
kubectl delete namespace ${NAMESPACE}
```

## Benchmarking Report

The benchmark runs on 16 × H200 GPUs, distributed across 8 model servers (2 H200s per server with
TP=2), serving Qwen3-VL-32B-Instruct on vLLM 0.23. Render is served by a standalone CPU
`vllm launch render` Service. The workload is a shared-prefix multimodal mix (cached and fresh
images) swept over an offered-QPS ladder.

### Render-Service Scaling

The render call runs on the routing path for every multimodal request. The saturation knee moves from
9 to 18 to 70 QPS across 1, 2, and 8 render-Service replicas.

<img src="./benchmark-results/render_scaling.png" width="640" alt="Render-replica scaling">

### Comparing Precise Affinity to a Simple Kubernetes Service

Graphs below compare precise-affinity routing to a stock Kubernetes Service that round-robins requests
across the same 8 vLLM pods (no EPP, no scoring).

<img src="./benchmark-results/throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./benchmark-results/latency_vs_qps.png" width="900" alt="Latency vs QPS">
<img src="./benchmark-results/ttft_p90_vs_qps.png" width="900" alt="TTFT p90 vs QPS">

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown</summary>

Output tokens/sec, higher is better; TTFT in seconds, lower is better.

| Offered QPS | k8s Output | Precise-affinity Output | k8s TTFT p90 | Precise-affinity TTFT p90 |
|-----:|-----------:|------------------------:|-------------:|--------------------------:|
| 40 | 2,560 | 2,560 | 0.7 | 1.3 |
| 60 | 3,842 | 3,841 | 2.1 | 2.8 |
| 80 | 5,121 | 4,685 | 2.5 | 14.6 |
| 120 | 6,390 | 4,479 | 3.1 | 18.8 |

</details>
