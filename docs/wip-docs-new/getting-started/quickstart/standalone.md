# Standalone Proxy Quickstart

This guide covers how to achieve a "hello-world" of llm-d with a standalone Envoy proxy.

> llm-d is also integrated with [Gateway API](./gateway.md) (which is ideal for online production services). For some use cases, however, a tight integration with the Gateway API adds complications (e.g. clusters using Ingress, RL post-training, or basic evaluations). Deployment as a standalone Envoy proxy as a sidecar to the EPP offers a simpler alternative.

## Overview

The Endpoint Picker (EPP) at its core is a smart request scheduler for LLM requests. It currently implements a number of LLM-specific load balancing optimizations including:

- **Prefix-cache aware scheduling**
- **Load-aware scheduling**

In standalone mode, a proxy is deployed as a sidecar to the EPP. The proxy and EPP communicate via ext-proc protocol over localhost.

For endpoint discovery, you have two options:

- **With Inference APIs Support**: The EPP is configured using the Inference CRDs. The pool is expressed using an instance of the InferencePool API and the entire suite of inference APIs are supported, including the use of InferenceObjectives for defining priorities.
- **Without Inference APIs Support**: The EPP is configured using command line flags. This is the simplest method for standalone jobs which doesn't require installing the inference extension APIs, which means no support for the features expressed using the inference APIs (such as InferenceObjectives).

## Prerequisites

A cluster with:

- Support for one of the three most recent Kubernetes minor [releases](https://kubernetes.io/releases/).
- Support for services of type `LoadBalancer`. For [kind](https://kind.sigs.k8s.io/) (Kubernetes IN Docker) clusters, follow [this guide](https://kind.sigs.k8s.io/docs/user/loadbalancer) to get services of type LoadBalancer working.
- Support for [sidecar containers](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/) (enabled by default since Kubernetes v1.29) to run the model server deployment.

Tools:

- [Helm](https://helm.sh/docs/intro/install/)
- [jq](https://jqlang.org/download/)

## Deploy a Sample Model Server

Choose one of the following options to deploy a sample model server.

### Option 1: GPU-Based vLLM Deployment

For this setup, you will need 3 GPUs to run the sample model server (one GPU per replica). Adjust the number of replicas as needed based on your available GPU resources.

Create a Hugging Face secret to download the model [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B).
You'll need a Hugging Face account and access token - see the [Hugging Face security tokens documentation](https://huggingface.co/docs/hub/security-tokens) for setup instructions.
Ensure that the token grants access to this model (you may need to request access for gated models).

Deploy a sample vLLM deployment with the proper protocol to work with the LLM Instance Gateway.

```bash
kubectl create secret generic hf-token --from-literal=token=$HF_TOKEN # Your Hugging Face Token with access to the set of Qwen models
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/gpu-deployment.yaml
```

### Option 2: CPU-Based vLLM Deployment

> **Note:** CPU deployment can be unreliable, i.e. the pods may crash/restart because of resource constraints.

This option uses the `vllm-cpu` image for x86 CPU platforms. We recommend approximately 9.5GB of memory and 12 CPUs for each replica, which gives reasonable response times. You can adjust the allocated resources in the `cpu-deployment.yaml` file. More memory and CPUs generally means better performance.

Deploy a sample vLLM deployment with the proper protocol to work with the LLM Instance Gateway.

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/cpu-deployment.yaml
```

### Option 3: vLLM Simulator Deployment

This option uses the [vLLM simulator](https://github.com/llm-d/llm-d-inference-sim/tree/main) to simulate a backend model server.
This setup uses the least amount of compute resources, does not require GPUs, and is ideal for test/dev environments.

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/sim-deployment.yaml
```

## Deploy EPP with Envoy Sidecar

Choose one of the following options to deploy an Endpoint Picker Extension with Envoy sidecar.

### Option 1: With Inference APIs Support

Deploy an InferencePool named `vllm-qwen3-32b` that selects from endpoints with label `app: vllm-qwen3-32b` and
listening on port 8000. The Helm install command automatically deploys an InferencePool instance, the EPP along with provider specific resources.

Set the chart version and then run the install:

```bash
# Install the Inference Extension CRDs
kubectl apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd

export STANDALONE_CHART_VERSION=v0
export PROVIDER=<YOUR_PROVIDER> # optional, can be gke as gke needs specific epp monitoring resources
helm install vllm-qwen3-32b-standalone \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=vllm-qwen3-32b \
  --set provider.name=$PROVIDER \
  --version $STANDALONE_CHART_VERSION \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/standalone
```

### Option 2: Without Inference APIs Support

Deploy an Endpoint Picker Extension named `vllm-qwen3-32b` that selects from endpoints with label `app=vllm-qwen3-32b` and listening on port 8000.
The Helm install command automatically deploys the EPP along with provider specific resources.

Set the chart version and then run the install:

```bash
export STANDALONE_CHART_VERSION=v0
export PROVIDER=<YOUR_PROVIDER> # optional, can be gke as gke needs specific epp monitoring resources
helm install vllm-qwen3-32b-standalone \
  --dependency-update \
  --set inferenceExtension.endpointsServer.endpointSelector="app=vllm-qwen3-32b" \
  --set inferenceExtension.endpointsServer.createInferencePool=false \
  --set provider.name=$PROVIDER \
  --version $STANDALONE_CHART_VERSION \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/standalone
```

## Make a Request

Wait until the EPP deployment is ready, then install the curl pod:

```bash
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: curl
  labels:
    app: curl
spec:
  containers:
  - name: curl
    image: curlimages/curl:7.83.1
    imagePullPolicy: IfNotPresent
    command:
      - tail
      - -f
      - /dev/null
  restartPolicy: Never
EOF
```

Send an inference request:

```bash
kubectl exec curl -- curl -i http://vllm-qwen3-32b-epp:8081/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "food-review-1","prompt": "Write as if you were a critic: San Francisco","max_tokens": 100,"temperature": 0}'
```

## Cleanup

Run the following commands to remove all resources created by this guide.

> **Warning:** Be careful not to delete resources you'd like to keep.

```bash
helm uninstall vllm-qwen3-32b-standalone
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/inferenceobjective.yaml --ignore-not-found
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/cpu-deployment.yaml --ignore-not-found
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/gpu-deployment.yaml --ignore-not-found
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/raw/main/config/manifests/vllm/sim-deployment.yaml --ignore-not-found
kubectl delete -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd --ignore-not-found
kubectl delete secret hf-token --ignore-not-found
kubectl delete pod curl --ignore-not-found
```
