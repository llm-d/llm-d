# Istio

This guide shows how to deploy llm-d with [Istio](https://istio.io/) as your inference gateway. By the end, inference requests will flow from an Istio-managed `Gateway` to your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

* A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
* [Helm](https://helm.sh/docs/intro/install/)
* [jq](https://jqlang.org/download/)

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required Gateway API and Gateway API Inference Extension CRDs:

```bash
GATEWAY_API_VERSION=v1.5.1
GAIE_VERSION=v1.4.0

kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=${GATEWAY_API_VERSION}"
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

Verify the APIs are available:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

## Step 2: Install Istio

Install Istio with inference extension support enabled:

```bash
ISTIO_VERSION=1.29.0
curl -L https://istio.io/downloadIstio | ISTIO_VERSION=${ISTIO_VERSION} sh -
export PATH="$PWD/istio-${ISTIO_VERSION}/bin:$PATH"
istioctl install -y \
  --set values.pilot.env.ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true
```

Verify the installation:

```bash
kubectl get pods -n istio-system
```

Expected output:

```text
NAME                      READY   STATUS    RESTARTS   AGE
istiod-xxxxxxxxxx-xxxxx   1/1     Running   0          30s
```

## Step 3: Deploy Model Servers

Deploy two replicas of vLLM running `Qwen/Qwen3-0.6B`:

> [!NOTE]
> This example uses NVIDIA GPUs. For CPU testing, use the vLLM Simulator (`ghcr.io/llm-d/llm-d-inference-sim:latest`).

```bash
kubectl apply -f https://raw.githubusercontent.com/robertgshaw2-redhat/llm-d/clean-up-common-yamls/helpers/manifests/vllm-deployment.yaml
```

Verify the pods are running:

```bash
kubectl get pods -l app=my-model
```

## Step 4: Deploy the Gateway

Create a `Gateway` resource. Istio watches this resource and creates an Envoy-based proxy that accepts incoming traffic.

```bash
kubectl apply -k "https://github.com/robertgshaw2-redhat/llm-d/helpers/manifests/gateway/istio?ref=clean-up-common-yamls"
```

Verify the Gateway is programmed:

```bash
kubectl get gateway llm-d-inference-gateway
```

Expected output:

```text
NAME                      CLASS   ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   istio   10.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 5: Deploy an InferencePool and EPP

Deploy the `InferencePool` and EPP with the Helm chart, using `provider.name=istio`:

```bash
IGW_CHART_VERSION=v1.4.0

helm install llm-d-infpool \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=my-model \
  --set provider.name=istio \
  --version ${IGW_CHART_VERSION} \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

Verify the EPP is running and the `InferencePool` is created:

```bash
kubectl get pods,inferencepool
```

Expected output:

```text
NAME                                     READY   STATUS    RESTARTS   AGE
pod/llm-d-infpool-epp-xxxxxxxxx-xxxxx    1/1     Running   0          30s

NAME                                                       AGE
inferencepool.inference.networking.k8s.io/llm-d-infpool    30s
```

The EPP pod shows `1/1` rather than `2/2` because there is no sidecar proxy in this setup. Istio manages the gateway proxy separately.

## Step 6: Configure the HTTPRoute

Create an `HTTPRoute` to connect the Gateway to the `InferencePool`. When traffic reaches the `Gateway` with this route, the Proxy will consult the EPP and forward the request to the selected pod.

```bash
kubectl apply -f https://raw.githubusercontent.com/robertgshaw2-redhat/llm-d/clean-up-common-yamls/helpers/manifests/httproute/httproute.yaml
```

Verify the HTTPRoute is accepted:

```bash
kubectl get httproute llm-d-route -o yaml | grep -A5 "conditions:"
```

Both `Accepted` and `ResolvedRefs` conditions should show `status: "True"`.

## Step 7: Send a Request

Get the Gateway's external address:

```bash
export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request through the Istio Gateway:

```bash
curl -s http://${GATEWAY_IP}/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-0.6B",
    "messages": [{"role": "user", "content": "Hello, who are you?"}],
    "max_tokens": 50
  }'
```

## Cleanup

```bash
kubectl delete httproute llm-d-route
helm uninstall llm-d-infpool
kubectl delete gateway llm-d-inference-gateway
kubectl delete deployment my-model
istioctl uninstall --purge -y
kubectl delete namespace istio-system
kubectl delete gatewayclass istio istio-remote
kubectl delete -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=${GATEWAY_API_VERSION}"
kubectl delete -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway
kubectl get pods -n istio-system
kubectl logs -n istio-system deployment/istiod --tail=20
```

Verify Istio was installed with the inference extension flag enabled.

### EPP pod in CrashLoopBackOff

```bash
kubectl logs <epp-pod-name> --tail=20
```

Common causes:
* InferencePool not created: check `kubectl get inferencepool`
* CRDs not installed: check `kubectl get crd | grep inference`

### HTTPRoute not accepted

```bash
kubectl describe httproute llm-d-route
```

Verify that `parentRefs` matches the Gateway name and `backendRefs` matches the InferencePool name.

### No response from Gateway IP

```bash
kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}'
```

If the address is empty, your Gateway may still be waiting for a LoadBalancer service. Check that your cluster supports external load balancers.

