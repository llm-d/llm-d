# Quickstart

This is a quickstart for deploying a "hello, world" llm-d deployment with a **standalone Envoy proxy**.

> [!NOTE]
> Looking for production deployment with a Gateway API instead? See the [Gateway Configuration Guide](XXXX) for more details.

## Request Flow

In the llm-d architecture, requests flow in the following way:
- Client sends a request (e.g. `/v1/chat/completions`) to the Envoy Proxy
- The Proxy queries the EndpointPicker which selects the optimal replica to process the request from the InferencePool. In the standalone mode, the Envoy proxy and the EPP are running as two containers inside one Kubernetes Pod. In the Gateway mode, the EPP runs independently from the proxy (aka the Gateway).
- The Proxy sends the request to the vLLM pod in the InferencePool, which processes the query

```
        ┌─────────┐
        │ Client  │
        └────┬────┘
             │
             ▼     
        ┌─────────┐      ┌─────┐
        │  Proxy  │◄────►│ EPP │
        └────┬────┘      └─────┘
             │           
             ▼
  ┌────────────────────────────────┐
  │  ┌──────┐ ┌──────┐   ┌──────┐  │ 
  │  │ vLLM │ │ vLLM │...│ vLLM │  │
  │  └──────┘ └──────┘   └──────┘  │
  └────────────────────────────────┘
```

## Prerequisites

A Kubernetes cluster with:
- Support for one of the three most recent Kubernetes minor [releases](https://kubernetes.io/releases/).
- [Helm](https://helm.sh/docs/intro/install/)
- [jq](https://jqlang.org/download/)

> [!NOTE]
> The example below uses a NVIDIA GPU deployment for vLLM, but you can leverage the vLLM Simulator (`ghcr.io/llm-d/llm-d-inference-sim:latest`) for basic CPU based testing.

## Install

llm-d leverages the APIs defined by Gateway API Inference Extension. Install them:

```bash
kubectl apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd
```

## Deploy

First, deploy the llm-d inference scheduler, you need one per InferencePool:

```bash
export STANDALONE_CHART_VERSION=v0

helm install my-inference-pool \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=my-model \
  --version $STANDALONE_CHART_VERSION \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/standalone
```

- The inference scheduler for a pool named `my-inference-pool` is deployed and will add all pods with labels `app=my-model`.
- The EPP is deployed with the default configuration (which uses prefix-cache aware and load-aware balancing).
- The Proxy is deployed as a sidecar in the EPP pod.

```
>> TODO: kubectl view the resources
```

Next, create the model servers Deployment (in this case, 2 replicas of vLLM running `openai/gpt-oss-20b`):

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-model
spec:
  replicas: 2
  selector:
    matchLabels:
      # Used by the InferencePool for service discovery
      app: my-model
  template:
    metadata:
      labels:
        app: my-model
        # Used by the InferencePool for selecting the metrics mapping
        inference.networking.k8s.io/engine-type: vllm
    spec:
      containers:
        - name: vllm
          image: "vllm/vllm-openai:latest"
          imagePullPolicy: Always
          command: ["vllm serve openai/gpt-oss-20b"]
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

- A deployment with 2 replicas of vLLM is created
- The model servers pods are automatically discovered by the EPP via the `app:my-model` selector.

```
>> TODO: kubectl view the resources, and show they were added to the InfPool
```


## Make a Request

Install the curl pod.

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

Send an inference request.

```bash
>> TODO: write an inference request
```

## Cleanup

Run the following commands to remove all resources created by this guide.

```bash
helm uninstall my-inference-pool
kubectl delete deployments my-model
kubectl delete -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd --ignore-not-found
kubectl delete pod curl --ignore-not-found
```
