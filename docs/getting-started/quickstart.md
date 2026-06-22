# Quickstart

This guide provides a simplified, end-to-end walkthrough for deploying an **Optimized Baseline** configuration using llm-d. This setup reduces tail latency and increases throughput through load-aware and prefix-cache aware balancing.

For this quickstart, we will use the **Standalone Mode** deployment, which is the easiest way to get started with llm-d.

## Prerequisites

- Ensure you have a Kubernetes cluster and `kubectl` configured.
- Install [Helm](https://helm.sh/docs/intro/install/).
- Install [jq](https://jqlang.org/download/).

### 1. Setup Environment

Clone the llm-d repository and set up the necessary environment variables:

```bash
git clone https://github.com/llm-d/llm-d.git && cd llm-d

export GAIE_VERSION=v1.5.0
export ROUTER_CHART_VERSION=v0
export GUIDE_NAME="quickstart"
export NAMESPACE=llm-d-quickstart
```

### 2. Install CRDs and Namespace

Install the Gateway API Inference Extension CRDs and create the namespace:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
kubectl create namespace ${NAMESPACE}
```

## Installation

### 1. Deploy the llm-d Router (Standalone Mode)

The llm-d Router provides the intelligent load balancing. In Standalone Mode, it includes a built-in proxy (Envoy).

```bash
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f guides/recipes/router/base.values.yaml \
    -f guides/optimized-baseline/router/optimized-baseline.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

### 2. Deploy the Model Server

Deploy the default model server (vLLM running on NVIDIA GPUs). This will deploy 8 replicas of `Qwen/Qwen3-32B` by default.

```bash
kubectl apply -n ${NAMESPACE} -k guides/optimized-baseline/modelserver/gpu/vllm/
```

> [!TIP]
> If you are using different hardware (AMD, Intel, TPU, or CPU), you can find alternative configurations in the `guides/optimized-baseline/modelserver/` directory.

## Verification

### 1. Get the IP of the Proxy

Retrieve the ClusterIP of the llm-d Router service:

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send a Test Request

Open a temporary interactive shell inside the cluster to send a request:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    -- /bin/bash
```

Inside the shell, send a completion request:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-32B",
        "prompt": "How are you today?"
    }' | jq
```

## Cleanup

To remove all resources created in this guide:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```
