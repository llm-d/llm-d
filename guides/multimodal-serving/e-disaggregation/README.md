# E-Disaggregation (Encode Disaggregation)

## Overview

This experimental guide deploys `Qwen/Qwen3-VL-32B-Instruct` with encode disaggregation for multimodal inference workloads. Encode disaggregation offloads the multimodal encoding stage (converting raw images, video, or audio into embeddings) to dedicated workers. The resulting embeddings are then consumed by prefill/decode workers alongside text tokens. When a request contains multiple multimodal entries, they can be processed concurrently by different Encode workers, reducing overall latency.

llm-d supports two encode-disaggregated topologies:

| Topology | Description | Workers |
| -------- | ----------- | ------- |
| **E/PD** | Encode separated from Prefill+Decode | Encode workers + PD workers |
| **E/P/D** | Full three-stage pipeline | Encode workers + Prefill workers + Decode workers |

> [!NOTE]
> The Encode (E) stage is only relevant for requests with multimodal content (images, video, or audio). For text-only requests, the encode stage is skipped regardless of the configured topology.

> [!WARNING]
> Encode disaggregation is under active development in both vLLM and llm-d Router.

### E/PD Configuration

In E/PD, dedicated encode workers handle multimodal processing while a single worker type handles both prefill and decode. Multiple encode workers enable parallel processing of multimodal entries within a single request:

* 2 Encode Workers (multimodal encoding, parallelized across entries)
* 8 TP=2 Decode Workers (prefill + decode combined)

### E/P/D Configuration

E/P/D extends P/D disaggregation by adding a dedicated encode stage. This provides maximum specialization, with multiple encode workers processing multimodal content in parallel:

* 2 Encode Workers (multimodal encoding, parallelized across entries)
* 4 TP=4 Prefill Workers
* 2 TP=4 Decode Workers

### Best Practices

Encode disaggregation is most beneficial for workloads with:

* **Multimodal content** - requests containing images, video, or audio that require significant encoding compute
* **High multimodal-to-text ratio** - workloads where a large fraction of requests contain multimodal inputs
* **Large vision models** - models where the vision encoder is expensive relative to text processing (e.g. large ViT backbones)

Choose between topologies:

* **E/PD** - simpler deployment; best when prefill and decode do not need separate scaling, or when the primary bottleneck is encode
* **E/P/D** - extends the [P/D Disaggregation](../../pd-disaggregation/README.md) guide by adding a dedicated encode stage. The reasons for separating prefill from decode (heterogeneous parallelism, xPyD ratios, workload specialization) are described in the [P/D Best Practices](../../pd-disaggregation/README.md#pd-best-practices) section

### Supported Hardware Backends

| Backend           | Directory                    | Notes                        |
| ----------------- | ---------------------------- | ---------------------------- |
| NVIDIA GPU (vLLM) | `modelserver/gpu/vllm/`      | vLLM with encode disagg      |

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.
- Checkout llm-d repo:
```bash
export branch="main" # branch, tag, or commit hash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
```
- Set the following environment variables (choose your topology):

**For E/PD:**
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export RELEASE_NAME="e-disaggregation"
export GUIDE_PATH="multimodal-serving/e-disaggregation"
export TOPOLOGY="e-pd"
export NAMESPACE="llm-d-e-pd-disaggregation"
export MODEL_NAME="Qwen/Qwen3-VL-32B-Instruct"
```

**For E/P/D:**
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export RELEASE_NAME="e-disaggregation"
export GUIDE_PATH="multimodal-serving/e-disaggregation"
export TOPOLOGY="e-p-d"
export NAMESPACE="llm-d-e-p-d-disaggregation"
export MODEL_NAME="Qwen/Qwen3-VL-32B-Instruct"
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

- Install the Gateway API Inference Extension CRDs:
```bash
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```
- Create a target namespace for the installation:
```bash
kubectl create namespace ${NAMESPACE}
```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models.
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=<your HuggingFace token>
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${RELEASE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_PATH}/router/${TOPOLOGY}-disaggregation.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To employ a Kubernetes Gateway managed proxy instead of the standalone one, then instead of applying the standalone helm chart above, do the following:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster, all guides can share one Gateway each with a separate HTTPRoute.
2. *Deploy the llm-d Router and an HTTPRoute*. The following deploys the llm-d Router with an HTTPRoute that connects it to the Gateway created in the previous step (set `provider.name` to the gateway provider you deployed):

```bash
export PROVIDER_NAME=gke # other: na, agentgateway, or istio
helm install ${RELEASE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_PATH}/router/${TOPOLOGY}-disaggregation.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your chosen topology:

```bash
export INFRA_PROVIDER=gke # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/gpu/vllm/${TOPOLOGY}/${INFRA_PROVIDER}/
```

### 3. Enable Monitoring (optional)

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../../docs/operations/observability) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../../docs/operations/observability).
- Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
```

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${RELEASE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a multimodal request (image):**

```bash
curl -X POST http://${IP}/v1/chat/completions \
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

**Send a text-only request (encode stage will be skipped):**

```bash
curl -X POST http://${IP}/v1/chat/completions \
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

To remove the deployed components:

```bash
helm uninstall ${RELEASE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/gpu/vllm/${TOPOLOGY}/${INFRA_PROVIDER}/
```

If you deployed in Gateway Mode, also remove the Gateway by following [the gateway cleanup guide](../../../docs/infrastructure/gateway/gke.md#cleanup).

## Architecture

### EC Connector
The EC Connector is a high-level architectural interface designed to transfer encoder outputs (such as image, video, or audio embeddings) between a dedicated producer (an Encode Worker) and downstream consumers (Prefill or Decode Workers).
When serving multimodal models, processing the media inputs is highly compute-intensive. 
The EC Connector allows vLLM to physically separate the "Encode" phase from other phases. 
Once the Encode Worker processes a multimodal item, the EC Connector handles the orchestration of sharing those resulting embedding references across the network, preventing the Prefill or Decode pods from having to recompute the same visual inputs.

This guide uses ECCPU connector. The ECCPU Connector is a distributed transfer mechanism that allows a consumer vLLM instance to efficiently fetch pre-computed encoder outputs from a remote producer instance 
using a high-performance NIXL data plane and ZMQ control plane. By sharing these cached outputs across CPU memory-mapped regions, it enables consumer instances to bypass redundant encoding tasks and speed up inference.

### E/PD Request Flow

```
Client -> Envoy -> EPP -> Decode Worker Sidecar
                              |
                              +-> Encode Worker (multimodal content)
                              |       |
                              |       | === EC_Connector ===
                              |       |  ZMQ (Control): XferReq / XferAck
                              |       |  NIXL (Data): Direct memory write
                              |       |  v (embedding references)
                              +-> Decode Worker (prefill + decode locally)
                              |
                              v
                          Response -> Client
```

1. Client sends a multimodal inference request via the OpenAI API
2. EPP's `disagg-profile-handler` selects a decode pod, then the encode decider detects multimodal content and selects an encode pod
3. Request lands on the Decode Worker's sidecar, which sends encoding work to the selected Encode Worker via the `x-encoder-hosts-ports` header
4. Encode Worker processes multimodal content and returns encoding metadata (embedding references)
5. Decode Worker reads embeddings via EC_Connector and runs prefill + decode locally

The ECCPU Connector is a distributed transfer mechanism that allows a consumer vLLM instance to efficiently fetch pre-computed encoder outputs from a remote producer instance using a high-performance NIXL data plane and ZMQ control plane. 
By sharing these cached outputs across CPU memory-mapped regions, it enables consumer instances to bypass redundant encoding tasks and speed up inference.
### E/P/D Request Flow

```
Client -> Envoy -> EPP -> Decode Worker Sidecar
                              |
                              +-> Encode Worker (multimodal content)
                              |       |
                              |       | === EC_Connector ===
                              |       |  ZMQ (Control): XferReq / XferAck
                              |       |  NIXL (Data): Direct memory write
                              |       |  v (embedding references)
                              +-> Prefill Worker (reads embeddings, runs prefill)
                              |       |
                              |       v (KV cache transfer)
                              +-> Decode Worker (decode only)
                              |
                              v
                          Response -> Client
```

1. Client sends a multimodal inference request via the OpenAI API
2. EPP's `disagg-profile-handler` runs all three stages: selects decode pod, encode pod (if multimodal), and prefill pod (if disaggregation is beneficial)
3. Sidecar sends multimodal content to Encode Worker
4. Encode Worker returns embedding references
5. Sidecar sends prefill request (with embedding metadata) to Prefill Worker
6. Prefill Worker reads embeddings via EC_Connector, runs prefill, returns KV parameters
7. Decode Worker reads KV cache from Prefill Worker and runs decode

## References

- [llm-d Router Disaggregation Docs](https://github.com/llm-d/llm-d-router/blob/main/docs/disaggregation.md)
- [vLLM: Disaggregated Encoder](https://docs.vllm.ai/en/latest/features/disagg_encoder/)
- [vLLM: Disaggregated Prefill](https://docs.vllm.ai/en/latest/features/disagg_prefill/)
- [vLLM: Encoder Disaggregation for Scalable Multimodal Model Serving](https://vllm.ai/blog/vllm-epd)
