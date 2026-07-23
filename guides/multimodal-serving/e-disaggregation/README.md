# NVIDIA GPU vLLM E-Disaggregation

## Overview

This experimental guide deploys `Qwen/Qwen3-VL-32B-Instruct` with vLLM encode
disaggregation on NVIDIA GPUs. Encode disaggregation offloads multimodal
encoding to dedicated workers. The resulting embeddings are consumed by
prefill/decode workers alongside text tokens.

For the SGLang E/PD configuration with Intel XPU Encode workers and an NVIDIA
GPU PD worker, use the
[heterogeneous SGLang guide](./heterogeneous/sglang/README.md).

This guide supports two vLLM topologies:

| Topology | Description | Workers |
| --- | --- | --- |
| **E/PD** | Encode separated from Prefill+Decode | Encode workers + PD workers |
| **E/P/D** | Full three-stage pipeline | Encode workers + Prefill workers + Decode workers |

The Encode stage is only relevant for requests with multimodal content. For
text-only requests, it is skipped regardless of the configured topology.

> [!WARNING]
> Encode disaggregation is under active development in both vLLM and the
> llm-d Router.

### E/PD Configuration

In E/PD, dedicated Encode workers handle multimodal processing while a single
worker type handles both prefill and decode. Multiple Encode workers enable
parallel processing of multimodal entries within a single request:

* 2 TP=2 Encode workers
* 8 TP=2 PD workers

### E/P/D Configuration

E/P/D extends P/D disaggregation by adding a dedicated Encode stage:

* 2 TP=2 Encode workers
* 3 TP=2 Prefill workers
* 3 TP=2 Decode workers

### Best Practices

Encode disaggregation is most beneficial for workloads with:

* **Multimodal content**: images, video, or audio that require significant
  encoding compute.
* **High multimodal-to-text ratio**: a large fraction of requests contain
  multimodal inputs.
* **Large vision models**: the vision encoder is expensive relative to text
  processing.

Choose between topologies:

* **E/PD**: simpler deployment when prefill and decode do not need independent
  scaling or the primary bottleneck is Encode.
* **E/P/D**: adds a dedicated Encode stage to the
  [P/D Disaggregation](../../pd-disaggregation/README.md) topology. See
  [P/D Best Practices](../../pd-disaggregation/README.md#pd-best-practices)
  for the reasons to separate prefill and decode.

### Supported Hardware Backends

| Backend | Directory | Notes |
| --- | --- | --- |
| NVIDIA GPU (vLLM) | `modelserver/gpu/vllm/` | vLLM with encode disaggregation |

## Prerequisites

* Install the [client tools](../../../helpers/client-setup/README.md).
* Clone and check out the llm-d repository:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git
  cd llm-d
  git checkout "${branch}"
  ```

* Set the environment variables for the selected topology.

  For E/PD:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source "${REPO_ROOT}/guides/env.sh"
  export RELEASE_NAME="e-disaggregation"
  export GUIDE_PATH="multimodal-serving/e-disaggregation"
  export TOPOLOGY="e-pd"
  export NAMESPACE="llm-d-e-pd-disaggregation"
  export MODEL_NAME="Qwen/Qwen3-VL-32B-Instruct"
  ```

  For E/P/D:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source "${REPO_ROOT}/guides/env.sh"
  export RELEASE_NAME="e-disaggregation"
  export GUIDE_PATH="multimodal-serving/e-disaggregation"
  export TOPOLOGY="e-p-d"
  export NAMESPACE="llm-d-e-p-d-disaggregation"
  export MODEL_NAME="Qwen/Qwen3-VL-32B-Instruct"
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
  ```

* Create a target namespace:

  ```bash
  kubectl create namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```

* [Create the `llm-d-hf-token` secret](../../../helpers/hf-token.md) in the
  target namespace with the key `HF_TOKEN`.

  <!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your Hugging Face token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
  <!-- llm-d-cicd:skip end -->

## Installation

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar without creating a
Kubernetes Gateway:

```bash
helm install "${RELEASE_NAME}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/${GUIDE_PATH}/router/${TOPOLOGY}-disaggregation.values.yaml" \
  -n "${NAMESPACE}" \
  --version "${ROUTER_CHART_VERSION}"
```

<details>
<summary><b>Gateway Mode</b></summary>

Deploy a Kubernetes Gateway by following a
[gateway provider guide](../../../docs/infrastructure/gateway), then install
the Router and HTTPRoute:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install "${RELEASE_NAME}" \
  "${ROUTER_GATEWAY_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml" \
  -f "${REPO_ROOT}/guides/${GUIDE_PATH}/router/${TOPOLOGY}-disaggregation.values.yaml" \
  --set "provider.name=${PROVIDER_NAME}" \
  -n "${NAMESPACE}" \
  --version "${ROUTER_CHART_VERSION}"
```

</details>

### 2. Deploy the Model Servers

Apply the Kustomize overlay for the selected topology:

```bash
export INFRA_PROVIDER=gke # options: base, gke
kubectl apply -n "${NAMESPACE}" \
  -k "${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/gpu/vllm/${TOPOLOGY}/${INFRA_PROVIDER}"
```

### 3. Enable Monitoring (Optional)

* Install the [monitoring stack](../../../docs/operations/observability).
* Add
  `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml`
  during Router installation to monitor the Router.
* Deploy the model-server PodMonitors:

  ```bash
  kubectl apply -n "${NAMESPACE}" \
    -k "${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd"
  ```

## Verification

### 1. Get the Proxy Address

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

### 2. Send Test Requests

Open a temporary interactive shell in the cluster:

```bash
kubectl run curl-debug --rm -it \
  --namespace "${NAMESPACE}" \
  --image=cfmanteiga/alpine-bash-curl-jq \
  --env="IP=${IP}" \
  -- /bin/bash
```

Send a multimodal request:

```bash
curl -X POST "http://${IP}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-VL-32B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "image_url",
            "image_url": {
              "url": "https://images.dog.ceo/breeds/retriever-golden/n02099601_3004.jpg"
            }
          },
          {
            "type": "text",
            "text": "What is in this image?"
          }
        ]
      }
    ],
    "max_tokens": 128
  }' | jq
```

Send a text-only request. The Encode stage is skipped:

```bash
curl -X POST "http://${IP}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-VL-32B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": "How are you today?"
      }
    ],
    "max_tokens": 128
  }' | jq
```

## Cleanup

```bash
helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
kubectl delete -n "${NAMESPACE}" \
  -k "${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/gpu/vllm/${TOPOLOGY}/${INFRA_PROVIDER}"
```

If Gateway mode was used, follow the selected provider guide to remove the
Gateway when it is no longer shared by other deployments.

## Architecture

### EC Connector

The EC Connector transfers encoder outputs, such as image, video, or audio
embeddings, between dedicated Encode workers and downstream Prefill or Decode
workers.

This guide uses the ECCPU Connector. It uses a NIXL data plane and ZMQ control
plane to share cached encoder outputs through CPU memory-mapped regions,
allowing downstream workers to bypass redundant encoding.

### E/PD Request Flow

```text
Client -> Envoy -> EPP -> Decode Worker Sidecar
                              |
                              +-> Encode Worker (multimodal content)
                              |       |
                              |       | EC Connector
                              |       | ZMQ control + NIXL data
                              |       v
                              +-> PD Worker (prefill + decode)
                              |
                              v
                          Response -> Client
```

1. The client sends a multimodal OpenAI-compatible request.
2. The EPP disaggregation profile selects a Decode pod and an Encode pod.
3. The Decode worker sidecar sends Encode work through routing headers.
4. The Encode worker processes the multimodal content.
5. The PD worker reads the embeddings through the EC Connector and performs
   prefill and decode.

### E/P/D Request Flow

```text
Client -> Envoy -> EPP -> Decode Worker Sidecar
                              |
                              +-> Encode Worker
                              |       |
                              |       | ZMQ control + NIXL data
                              |       v
                              +-> Prefill Worker
                              |       |
                              |       v KV cache transfer
                              +-> Decode Worker
                              |
                              v
                          Response -> Client
```

1. The EPP selects Decode, Encode, and Prefill workers.
2. The sidecar sends multimodal content to the Encode worker.
3. The Encode output is transferred to the Prefill worker.
4. The Prefill worker returns KV transfer parameters.
5. The Decode worker receives KV cache state and performs decode.

## References

* [llm-d Router disaggregation](https://github.com/llm-d/llm-d-router/blob/main/docs/disaggregation.md)
* [vLLM disaggregated encoder](https://docs.vllm.ai/en/latest/features/disagg_encoder/)
* [vLLM disaggregated prefill](https://docs.vllm.ai/en/latest/features/disagg_prefill/)
* [Encoder Disaggregation for Scalable Multimodal Model Serving](https://vllm.ai/blog/vllm-epd)
