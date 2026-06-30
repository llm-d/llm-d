# Serving Multiple Inference Pools

## Overview

This guide demonstrates how to deploy multiple large language models (LLMs) in a cluster to support different workloads using the Inference-Payload-Processor (IPP) component.

A company may need to deploy multiple large language models (LLMs) in a cluster to support different workloads. For example, a Qwen model could power a chatbot interface, while a DeepSeek model might serve a recommendation application. Additionally, each base model may have multiple Low-Rank Adaptations ([LoRAs](https://www.ibm.com/think/topics/lora)). LoRAs associated with the same base model are served by the same backend inference server that hosts the base model. A LoRA name is also provided as the model name in the request body.

For serving multiple inference pools, the system needs to extract information such as the model name from the request body. This pattern of serving multiple models behind a single endpoint is common among providers and is generally expected by clients.

For such model-aware routing, use the Inference-Payload-Processor (IPP) component as described in this guide.

## Prerequisites

- Complete the [Getting Started guide](../optimized-baseline/README.md) before running this guide
- Have a running Kubernetes cluster with Gateway API installed
- Have `kubectl` and `helm` installed and configured

## Installation Instructions

### How It Works

The IPP extracts the model name from the request body, does a lookup of the base model in a configmap and adds this information in the `X-Gateway-Base-Model-Name` header. This header is then used for matching and routing the request to the appropriate `InferencePool` and its associated Endpoint Picker Extension (EPP) instances.

⚠️ **Note**: All model names, including base and LoRA names must be unique in order to be able to understand what is the correct `InferencePool` that should receive the request.

### Deploy Inference-Payload-Processor

   ```bash
   export CHART_VERSION=<SELECTED_VERSION>
   ```

<details>
<summary><b>GKE</b></summary>

```bash
export GATEWAY_PROVIDER=gke
helm install payload-processor \
--set provider.name=$GATEWAY_PROVIDER \
--version $CHART_VERSION \
oci://ghcr.io/llm-d/charts/payload-processor
```

</details>

<details>
<summary><b>Istio</b></summary>

```bash
export GATEWAY_PROVIDER=istio
helm install payload-processor \
--set provider.name=$GATEWAY_PROVIDER \
--version $CHART_VERSION \
oci://ghcr.io/llm-d/charts/payload-processor
```

</details>

<details>
<summary><b>Agentgateway</b></summary>

Agentgateway does not require the Inference-Payload-Processor Extension, and instead natively implements the same functionality.
To use AgentGateway, apply an `AgentgatewayPolicy`:

```bash
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: ipp
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: inference-gateway
  traffic:
    phase: PreRouting
    transformation:
      request:
        set:
        - name: X-Gateway-Base-Model-Name
          value: |
            {"Qwen/Qwen3-32B": "Qwen/Qwen3-32B", "food-review-1": "Qwen/Qwen3-32B", "deepseek/DeepSeek-r1": "deepseek/DeepSeek-r1", "ski-resorts": "deepseek/DeepSeek-r1", "movie-critique": "deepseek/DeepSeek-r1"}[string(json(request.body).model)]
EOF
```

</details>

<details>
<summary><b>Other</b></summary>

```bash
helm install payload-processor \
--version $CHART_VERSION \
oci://ghcr.io/llm-d/charts/payload-processor
```

</details>

### Serving a Second Model Server

The example uses a vLLM simulator since this is the least common denominator configuration that can be run in every environment.
The manifest uses `deepseek/DeepSeek-r1` as base model with two LoRA adapters `ski-resorts` and `movie-critique`.

Deploy the second model server along with a mapping from LoRA adapters to the base model:

```bash
kubectl apply -f modelserver/sim-deployment.yaml
```

### Deploy the Second InferencePool and Endpoint Picker Extension

In order to create the `HttpRoute` mapping via the helm chart, one should use the experimental flag (`experimentalHttpRoute`) to specify the
base model that should be associated with the `InferencePool`.

Set the Helm chart version (unless already set).

   ```bash
   export IGW_CHART_VERSION=v0
   ```

<details>
<summary><b>GKE</b></summary>

```bash
export GATEWAY_PROVIDER=gke
helm install vllm-deepseek-r1 \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-deepseek-r1 \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=deepseek/DeepSeek-r1 \
--version $IGW_CHART_VERSION \
oci://ghcr.io/llm-d/charts/inferencepool
```

</details>

<details>
<summary><b>Istio</b></summary>

```bash
export GATEWAY_PROVIDER=istio
helm install vllm-deepseek-r1 \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-deepseek-r1 \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=deepseek/DeepSeek-r1 \
--version $IGW_CHART_VERSION \
oci://ghcr.io/llm-d/charts/inferencepool
```

</details>

<details>
<summary><b>Agentgateway</b></summary>

```bash
export GATEWAY_PROVIDER=none
helm install vllm-deepseek-r1 \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-deepseek-r1 \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=deepseek/DeepSeek-r1 \
--version $IGW_CHART_VERSION \
oci://ghcr.io/llm-d/charts/inferencepool
```

</details>

<details>
<summary><b>Other</b></summary>

```bash
helm install vllm-deepseek-r1 \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-deepseek-r1 \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=deepseek/DeepSeek-r1 \
--version $IGW_CHART_VERSION \
oci://ghcr.io/llm-d/charts/inferencepool
```

</details>

After the installation, verify that you have two `InferencePools` and two EPP pods, one per base model type, running without errors

```bash
kubectl get inferencepools
```

```bash
kubectl get pods
```

### Upgrade the First InferencePool and Endpoint Picker Extension

Update the first model server mapping of the LoRA adapters to the base model:

```bash
kubectl apply -f modelserver/configmap.yaml
```

Run `helm upgrade` in order to update in place the HttpRoute mapping of the first `InferencePool`:

<details>
<summary><b>GKE</b></summary>

```bash
export GATEWAY_PROVIDER=gke
helm upgrade vllm-qwen3-32b oci://ghcr.io/llm-d/charts/inferencepool \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-qwen3-32b \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=Qwen/Qwen3-32B \
--version $IGW_CHART_VERSION
```

</details>

<details>
<summary><b>Istio</b></summary>

```bash
export GATEWAY_PROVIDER=istio
helm upgrade vllm-qwen3-32b oci://ghcr.io/llm-d/charts/inferencepool \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-qwen3-32b \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=Qwen/Qwen3-32B \
--version $IGW_CHART_VERSION
```

</details>

<details>
<summary><b>Agentgateway</b></summary>

```bash
export GATEWAY_PROVIDER=none
helm upgrade vllm-qwen3-32b oci://ghcr.io/llm-d/charts/inferencepool \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-qwen3-32b \
--set provider.name=$GATEWAY_PROVIDER \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=Qwen/Qwen3-32B \
--version $IGW_CHART_VERSION
```

</details>

<details>
<summary><b>Other</b></summary>

```bash
helm upgrade vllm-qwen3-32b oci://ghcr.io/llm-d/charts/inferencepool \
--dependency-update \
--set inferencePool.modelServers.matchLabels.app=vllm-qwen3-32b \
--set experimentalHttpRoute.enabled=true \
--set experimentalHttpRoute.baseModel=Qwen/Qwen3-32B \
--version $IGW_CHART_VERSION
```

</details>

## Verification

### Get Proxy IP

Get the gateway IP and port:

```bash
IP=$(kubectl get gateway/inference-gateway -o jsonpath='{.status.addresses[0].value}')
PORT=80
```

### Send Test Requests

<details>
<summary><b>Chat Completions API</b></summary>

1. Send a few requests to the Qwen model directly:

    ```bash
    curl -X POST -i ${IP}:${PORT}/v1/chat/completions \
         -H "Content-Type: application/json" \
         -d '{
                "model": "Qwen/Qwen3-32B",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [
                    {
                       "role": "developer",
                       "content": "You are a helpful assistant."
                    },
                    {
                        "role": "user",
                        "content": "Linux is said to be an open source kernel because "
                    }
                ]
             }'
    ```

2. Send a few requests to Deepseek model to test that it works, as follows:

    ```bash
    curl -X POST -i ${IP}:${PORT}/v1/chat/completions \
         -H "Content-Type: application/json" \
         -d '{
                "model": "deepseek/DeepSeek-r1",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [
                    {
                       "role": "developer",
                       "content": "You are a helpful assistant."
                    },
                    {
                        "role": "user",
                        "content": "Linux is said to be an open source kernel because "
                    }
                ]
             }'
    ```

3. Send a few requests to the LoRA of the Qwen model as follows:

    ```bash
    curl -X POST -i ${IP}:${PORT}/v1/chat/completions \
         -H "Content-Type: application/json" \
         -d '{
                "model": "food-review-1",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [
                    {
                       "role": "reviewer",
                       "content": "You are a helpful assistant."
                    },
                    {
                        "role": "user",
                        "content": "Write a review of the best restaurants in San-Francisco"
                    }
                ]
          }'
    ```

4. Send a few requests to one LoRA of the Deepseek model as follows:

    ```bash
    curl -X POST -i ${IP}:${PORT}/v1/chat/completions \
         -H "Content-Type: application/json" \
         -d '{
                "model": "movie-critique",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [
                    {
                       "role": "reviewer",
                       "content": "You are a helpful assistant."
                    },
                    {
                       "role": "user",
                       "content": "What are the best movies of 2025?"
                    }
                ]
          }'
    ```

5. Send a few requests to another LoRA of the Deepseek model as follows:

    ```bash
    curl -X POST -i ${IP}:${PORT}/v1/chat/completions \
         -H "Content-Type: application/json" \
         -d '{
                "model": "ski-resorts",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [
                    {
                       "role": "reviewer",
                       "content": "You are a helpful assistant."
                     },
                     {
                       "role": "user",
                       "content": "Tell me about ski deals"
                      }
                 ]
          }'
    ```

</details>

<details>
<summary><b>Completions API</b></summary>

1. Send a few requests to the Deepseek model:

     ```bash
     curl -X POST -i ${IP}:${PORT}/v1/completions \
          -H "Content-Type: application/json" \
          -d '{
                 "model": "deepseek/DeepSeek-r1",
                 "prompt": "What is the best ski resort in Austria?",
                 "max_tokens": 20,
                 "temperature": 0
          }'
     ```

2. Send a few requests to the first Deepseek LoRA as follows:

     ```bash
     curl -X POST -i ${IP}:${PORT}/v1/completions \
          -H "Content-Type: application/json" \
          -d '{
                 "model": "ski-resorts",
                 "prompt": "What is the best ski resort in Austria?",
                 "max_tokens": 20,
                 "temperature": 0
          }'
     ```

3. Send a few requests to the second Deepseek LoRA as follows:

     ```bash
     curl -X POST -i ${IP}:${PORT}/v1/completions \
          -H "Content-Type: application/json" \
          -d '{
                 "model": "movie-critique",
                 "prompt": "Tell me about movies",
                 "max_tokens": 20,
                 "temperature": 0
          }'
     ```

</details>

## Cleanup

To remove the deployed components:

```bash
helm uninstall payload-processor
helm uninstall vllm-deepseek-r1
helm uninstall vllm-qwen3-32b
kubectl delete -f modelserver/
```