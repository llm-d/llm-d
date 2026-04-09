# Gateway API Quickstart

This guide covers how to achieve a "hello-world" of llm-d integrated with [Gateway API](https://gateway-api.sigs.k8s.io/).

> Gateway API is an official Kubernetes project focused on L4 and L7 routing in Kubernetes. This project represents the next generation of Kubernetes Ingress, Load Balancing, and Service Mesh APIs. By leveraging the APIs defined by [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/), llm-d fits neatly into production-grade Kubernetes routing.

This guide is based on the upstream [Getting Started with an Inference Gateway](https://gateway-api-inference-extension.sigs.k8s.io/guides/) guide.

## Prerequisites

A Kubernetes cluster with:

- One of the three most recent Kubernetes minor releases
- Support for services of type `LoadBalancer` (for kind clusters, follow the [kind LoadBalancer guide](https://kind.sigs.k8s.io/docs/user/loadbalancer/))
- Support for [sidecar containers](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/) (enabled by default since Kubernetes v1.29)

Required tools:

- [Helm](https://helm.sh/docs/intro/install/)
- [jq](https://jqlang.github.io/jq/download/)

### Verify Prerequisites

```bash
# Ensure you're running a recent Kubernetes version
kubectl version --short

# Verify cluster connectivity — all nodes should be in Ready status
kubectl get nodes

# Verify required tools are installed
helm version
jq --version
```

## Install Infrastructure Prerequisites

### Set Latest Release Variable

```bash
IGW_LATEST_RELEASE=$(curl -s https://api.github.com/repos/kubernetes-sigs/gateway-api-inference-extension/releases \
  | jq -r '.[] | select(.prerelease == false) | .tag_name' \
  | sort -V \
  | tail -n1)
```

### Deploy a Sample Model Server

Choose one of the following model server options.

#### Option 1: GPU-Based vLLM

Requires 3 GPUs to run the sample model server (one GPU per replica). Adjust replica count based on available hardware.

You need a [Hugging Face](https://huggingface.co/) account and [access token](https://huggingface.co/docs/hub/en/security-tokens) with access to the [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) model.

```bash
export INFERENCE_POOL_NAME=vllm-qwen3-32b
export MODEL_NAME=Qwen/Qwen3-32B
kubectl create secret generic hf-token --from-literal=token=$HF_TOKEN
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/gpu-deployment.yaml
```

#### Option 2: CPU-Based vLLM

Uses the `vllm-cpu` image for x86 CPU platforms. Recommended: ~64GB memory and 48 CPUs per replica for reliable performance. The sample uses 9.5GB memory and 12 CPUs per replica which provides reasonable response times.

> **Note:** CPU deployment can be unreliable (pods may crash/restart due to resource constraints).

```bash
export INFERENCE_POOL_NAME=vllm-qwen3-32b
export MODEL_NAME=Qwen/Qwen3-32B
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/cpu-deployment.yaml
```

#### Option 3: vLLM Simulator

Uses the [vLLM simulator](https://github.com/llm-d/llm-d-inference-sim/tree/main) to simulate a backend model server. Requires the least compute resources, does not require GPUs, and is ideal for test/dev environments.

```bash
export INFERENCE_POOL_NAME=vllm-qwen3-32b
export MODEL_NAME=Qwen/Qwen3-32B
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/sim-deployment.yaml
```

### Install the Inference Extension CRDs

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${IGW_LATEST_RELEASE}/manifests.yaml
```

Verify the CRDs were installed successfully:

```bash
kubectl get crds | grep inference.networking.k8s.io
```

You should see output listing the inference-related CRDs.

### Install the Gateway

Choose one of the following gateway implementations.

#### Istio

Requirements: [Gateway API CRDs](https://gateway-api.sigs.k8s.io/guides/#installing-gateway-api) installed.

On Linux or MacOS:

```bash
ISTIO_VERSION=1.28.0
curl -L https://istio.io/downloadIstio | ISTIO_VERSION=${ISTIO_VERSION} sh -
./istio-$ISTIO_VERSION/bin/istioctl install \
   --set values.pilot.env.ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true
```

On Windows:

```bash
ISTIO_VERSION=1.28.0
wget https://storage.googleapis.com/istio-release/releases/$ISTIO_VERSION/istio-$ISTIO_VERSION-win.zip
unzip istioctl-$ISTIO_VERSION-win.zip
./istio-$ISTIO_VERSION/bin/istioctl.exe install \
   --set values.pilot.env.ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true
```

> **Note:** Istio v1.28.0 includes full support for InferencePool v1. This guide assumes you are using Istio v1.28.0 or later to ensure compatibility with the InferencePool API.

#### GKE

GKE comes with Gateway API support built-in, so you can skip this step and move to the next section ([Deploy an Inference Gateway](#deploy-an-inference-gateway)).

#### Agentgateway

Requirements: [Gateway API CRDs](https://gateway-api.sigs.k8s.io/guides/#installing-gateway-api) installed.

Set the Agentgateway version and install the CRDs:

```bash
AGW_VERSION=v1.0.0
helm upgrade -i --create-namespace --namespace agentgateway-system --version $AGW_VERSION agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds
```

Install Agentgateway:

```bash
helm upgrade -i --namespace agentgateway-system --version $AGW_VERSION agentgateway oci://cr.agentgateway.dev/charts/agentgateway --set inferenceExtension.enabled=true
```

#### NGINX Gateway Fabric

Requirements: [Gateway API CRDs](https://gateway-api.sigs.k8s.io/guides/#installing-gateway-api) installed.

Install NGINX Gateway Fabric with the Inference Extension enabled:

```bash
helm install ngf oci://ghcr.io/nginx/charts/nginx-gateway-fabric --create-namespace -n nginx-gateway --set nginxGateway.gwAPIInferenceExtension.enable=true
```

### Deploy an Inference Gateway

Choose the option corresponding to the gateway implementation you installed above.

#### Istio

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/istio/gateway.yaml
```

> **Note:** This feature is currently in an experimental phase and is not intended for production use. The implementation and user experience are subject to changes.

#### GKE

Enable the Google Kubernetes Engine API, Compute Engine API, and the Network Services API, and configure proxy-only subnets when necessary. See [Deploy Inference Gateways](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway) for detailed instructions.

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/gke/gateway.yaml
```

#### Agentgateway

[Agentgateway](https://agentgateway.dev/) is a Gateway API and Inference Gateway implementation.

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: inference-gateway
spec:
  gatewayClassName: agentgateway
  listeners:
  - name: http
    port: 80
    protocol: HTTP
EOF
```

#### NGINX Gateway Fabric

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/nginxgatewayfabric/gateway.yaml
```

For more information, see the [NGINX Gateway Fabric - Inference Gateway Setup guide](https://docs.nginx.com/nginx-gateway-fabric/how-to/gateway-api-inference-extension/).

#### Verify the Gateway

Confirm the Gateway was assigned an IP address and reports a `Programmed=True` status:

```bash
kubectl get gateway inference-gateway
```

Expected output:

```
NAME                CLASS               ADDRESS         PROGRAMMED   AGE
inference-gateway   inference-gateway   <MY_ADDRESS>    True         22s
```

## Deploy InferencePool and EPP

Install an InferencePool that selects the endpoints from the sample model server you deployed and listens on port 8000. The Helm install command automatically installs the EPP, InferencePool, and provider-specific resources.

```bash
export IGW_CHART_VERSION=${IGW_LATEST_RELEASE}
```

Choose the option corresponding to your gateway provider.

### Istio

```bash
export GATEWAY_PROVIDER=istio
helm install ${INFERENCE_POOL_NAME} \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${INFERENCE_POOL_NAME} \
  --set provider.name=$GATEWAY_PROVIDER \
  --set experimentalHttpRoute.enabled=true \
  --version $IGW_CHART_VERSION \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

### GKE

```bash
export GATEWAY_PROVIDER=gke
helm install ${INFERENCE_POOL_NAME} \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${INFERENCE_POOL_NAME} \
  --set provider.name=$GATEWAY_PROVIDER \
  --set experimentalHttpRoute.enabled=true \
  --version $IGW_CHART_VERSION \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

### Agentgateway

```bash
export GATEWAY_PROVIDER=none
helm install ${INFERENCE_POOL_NAME} \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${INFERENCE_POOL_NAME} \
  --set provider.name=$GATEWAY_PROVIDER \
  --set experimentalHttpRoute.enabled=true \
  --version $IGW_CHART_VERSION \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

### NGINX Gateway Fabric

```bash
export GATEWAY_PROVIDER=none
helm install ${INFERENCE_POOL_NAME} \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=${INFERENCE_POOL_NAME} \
  --set provider.name=$GATEWAY_PROVIDER \
  --set experimentalHttpRoute.enabled=true \
  --version $IGW_CHART_VERSION \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

### Verify HttpRoute and InferencePool Status

Verify the HttpRoute was successfully configured:

```bash
kubectl get httproute ${INFERENCE_POOL_NAME} -o yaml
```

The status should include `Accepted=True` and `ResolvedRefs=True`.

Verify the InferencePool is active before sending traffic:

```bash
kubectl get inferencepool ${INFERENCE_POOL_NAME} -o yaml
```

The status should include `Accepted=True` and `ResolvedRefs=True`.

### Deploy InferenceObjective (Optional)

Deploy the sample InferenceObjective which allows you to specify priority of requests:

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/inferenceobjective.yaml
```

## Make a Request

Wait until the gateway is ready, then test with a completion request:

```bash
IP=$(kubectl get gateway/inference-gateway -o jsonpath='{.status.addresses[0].value}')
PORT=80

curl -i ${IP}:${PORT}/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "'${MODEL_NAME}'",
    "prompt": "Write as if you were a critic: San Francisco",
    "max_tokens": 100,
    "temperature": 0
  }'
```

## Cleanup

The following instructions assume you would like to cleanup ALL resources created in this quickstart guide. Be careful not to delete resources you want to keep.

### Uninstall InferencePool, InferenceObjective, and Model Server

```bash
helm uninstall ${INFERENCE_POOL_NAME}
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/inferenceobjective.yaml --ignore-not-found
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/cpu-deployment.yaml --ignore-not-found
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/gpu-deployment.yaml --ignore-not-found
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/vllm/sim-deployment.yaml --ignore-not-found
kubectl delete secret hf-token --ignore-not-found
```

### Uninstall the Inference Extension CRDs

```bash
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${IGW_LATEST_RELEASE}/manifests.yaml --ignore-not-found
```

### Cleanup the Inference Gateway

Choose the option corresponding to your gateway implementation.

#### Istio

```bash
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/istio/gateway.yaml --ignore-not-found
```

Optionally uninstall all Istio resources:

```bash
istioctl uninstall -y --purge
kubectl delete ns istio-system
```

#### GKE

```bash
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/gke/gateway.yaml --ignore-not-found
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/gke/healthcheck.yaml --ignore-not-found
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/gke/gcp-backend-policy.yaml --ignore-not-found
```

#### Agentgateway

```bash
kubectl delete gateway inference-gateway --ignore-not-found
```

Optionally uninstall all Agentgateway resources:

```bash
helm uninstall agentgateway -n agentgateway-system
helm uninstall agentgateway-crds -n agentgateway-system
kubectl delete ns agentgateway-system
```

#### NGINX Gateway Fabric

Remove the Inference Gateway:

```bash
kubectl delete -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/nginxgatewayfabric/gateway.yaml --ignore-not-found
```

Uninstall NGINX Gateway Fabric:

```bash
helm uninstall ngf -n nginx-gateway
kubectl delete ns nginx-gateway
```
