# Gateway API Quickstart

This guide covers how to achieve a "hello-world" of llm-d integrated with [Gateway API](https://gateway-api.sigs.k8s.io/).

> Gateway API is an official Kubernetes project focused on L4 and L7 routing in Kubernetes. This project represents the next generation of Kubernetes Ingress, Load Balancing, and Service Mesh APIs. By leveraging the APIs defined by [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/), llm-d fits neatly into production-grade Kubernetes routing.

## Prerequisites

A Kubernetes cluster with:
- One of the three most recent Kubernetes minor releases
- Support for services of type `LoadBalancer` (for kind clusters, follow the [kind LoadBalancer guide](https://kind.sigs.k8s.io/docs/user/loadbalancer/))
- Support for [sidecar containers](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/) (enabled by default since Kubernetes v1.29)

Tools:
- [Helm](https://helm.sh/docs/intro/install/)
- [jq](https://jqlang.github.io/jq/download/)

## Install Infrastructrue

First, we will install the infrastructure needed to deploy llm-d with a Gateway:
- Inference Extension CRDs
- Gateway API CRDs
- A Gateway Provider

We will use the latest release:

```bash
IGW_LATEST_RELEASE=$(curl -s https://api.github.com/repos/kubernetes-sigs/gateway-api-inference-extension/releases \
  | jq -r '.[] | select(.prerelease == false) | .tag_name' \
  | sort -V \
  | tail -n1)
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

Multiple Gateway Providers support the Gateway API Inference Extension, see [full list](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/).

In this example, we will install Istio, but see instructions for:
- Istio --- link
- GKE Gateway --- link
- Agentgateway --- link
- NGINX Gateway Fabric --- link

#### Installing Gateway API

Ensure the [Gateway API CRDs](https://gateway-api.sigs.k8s.io/guides/#installing-gateway-api) are installed on your cluster.
 
#### Installing Istio

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


## Deploy Model Server

Deploy a `openai/gpt-oss-20b` with 2 replicas of vLLM:

```yaml
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpt-oss-20b
spec:
  replicas: 2
  selector:
    matchLabels:
      # Used by the InferencePool for service discovery
      app: vllm-gpt-oss-20b
  template:
    metadata:
      labels:
        app: vllm-gpt-oss-20b
        # Used by the InferencePool for selecting the metrics mapping
        inference.networking.k8s.io/engine-type: vllm
    spec:
      containers:
        - name: vllm
          image: "vllm/vllm-openai:latest"
          imagePullPolicy: Always
          command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
          args:
            - "--model"
            - "vllm-gpt-oss-20b"
            - "--tensor-parallel-size"
            - "1"
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          resources:
            limits:
              nvidia.com/gpu: 1
              ephemeral-storage: "100Gi"
            requests:
              nvidia.com/gpu: 1
              ephemeral-storage: "100Gi"
EOF
```

### Deploy an Inference Gateway

Choose the option corresponding to the gateway implementation you installed above.

#### Istio

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/refs/tags/${IGW_LATEST_RELEASE}/config/manifests/gateway/istio/gateway.yaml
```

> **Note:** This feature is currently in an experimental phase and is not intended for production use. The implementation and user experience are subject to changes.


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
