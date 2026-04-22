# GKE

This guide shows how to deploy llm-d with
[GKE Gateway](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway) as your inference gateway. By the end, inference requests will be forwarded by a GKE-managed `Gateway` to your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with
> [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

The following steps from the [GKE Gateways deployment documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway) and [GKE Inference Gateway deployment documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway) should be run:

1. [Verify your prerequisites](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#before-you-begin)
2. [Enable Gateway API in your cluster](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#enable-gateway)
3. [Verify your cluster](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#verify-internal)
4. [Configure a proxy-only subnet](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#configure_a_proxy-only_subnet)
5. [Prepare your environment](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#prepare-environment)

## Step 1: Install Gateway API Inference Extension CRDs

For GKE versions `1.34.0-gke.1626000` or later, the InferencePool CRD is automatically installed. For GKE versions earlier than `1.34.0-gke.1626000` install it as follows: 

```bash
GAIE_VERSION=v1.4.0

kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

Verify the APIs are available:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

## Step 2: Deploy Model Servers

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

## Step 3: Deploy the Gateway

The key choice for deployment is whether you want to create a regional internal Application Load Balancer - accessible only to workloads within your VPC (class name: `gke-l7-rilb`) - or a regional external Application Load Balancer - accessible to the internet (class name: `gke-l7-regional-external-managed`). Here is an example for creating a regional external one:


```bash
kubectl apply -k "https://github.com/robertgshaw2-redhat/llm-d/helpers/manifests/gateway/gke-l7-regional-external-managed?ref=clean-up-common-yamls"
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway
```

Expected output:

```text
NAME                      CLASS                              ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   xx.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.


## Step 4: Deploy the InferencePool and EPP

Deploy the `InferencePool` and EPP with the Helm chart using `provider.name=gke`.


```bash
IGW_CHART_VERSION=v1.4.0

helm upgrade --install llm-d-infpool \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=my-model \
  --set provider.name=gke \
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

The EPP pod shows `1/1` rather than `2/2` because there is no sidecar proxy in
this setup. GKE manages the gateway proxy separately.

## Step 5: Configure the HTTPRoute

Create an `HTTPRoute` to connect the `Gateway` to the `InferencePool`. When
traffic reaches the `Gateway` with this route, the proxy consults the EPP and
forwards the request to the selected pod.

```bash
kubectl apply -f https://raw.githubusercontent.com/robertgshaw2-redhat/llm-d/clean-up-common-yamls/helpers/manifests/httproute/httproute.yaml
```

Verify the `HTTPRoute` is accepted:

```bash
kubectl get httproute llm-d-route -o yaml | grep -A5 "conditions:"
```

Both `Accepted` and `ResolvedRefs` conditions should show `status: "True"`.

## Step 6: Send a Request

Get the `Gateway` external address:

```bash
export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request through the managed `Gateway`:

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
```
