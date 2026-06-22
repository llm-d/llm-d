# Istio

This guide shows how to deploy llm-d with [Istio](https://istio.io/) as your inference gateway. By the end, inference requests will flow from an Istio-managed `Gateway` to your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. The environment variables `${GUIDE_NAME}`, `${MODEL_NAME}` and `${NAMESPACE}` should be set as part of deploying one of the well-lit path guides.
2. A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
3. [Helm](https://helm.sh/docs/intro/install/)
4. [jq](https://jqlang.org/download/)

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required CRDs by following the [CRD installation guide](./install-crds.md).

## Step 2: Install Istio

Install Istio with inference extension support enabled:

```bash
ISTIO_VERSION=1.29.2
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

## Step 3: Deploy the Gateway

Set the llm-d version to match your deployment:

```bash
LLM_D_VERSION=main  # Use 'main' for latest, or a release tag like 'v0.7.0'
```

Create a `Gateway` resource. Istio watches this resource and creates an Envoy-based proxy that accepts incoming traffic.

```bash
kubectl apply -k "https://github.com/llm-d/llm-d/guides/recipes/gateway/istio?ref=${LLM_D_VERSION}" -n ${NAMESPACE}
```

Verify the Gateway is programmed:

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Expected output:

```text
NAME                      CLASS   ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   istio   10.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 4: Send a Request

> [!IMPORTANT]
> Before sending requests, you must deploy a well-lit path guide. This sets up a model server deployment, an `InferencePool`, and an `HTTPRoute` to connect the Gateway to the pool.

Get the `Gateway` external address:

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request via the managed `Gateway`:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": '\"${MODEL_NAME}\"',
        "prompt": "How are you today?"
    }' | jq
```

## Cleanup

```bash
kubectl delete gateway llm-d-inference-gateway -n ${NAMESPACE}
istioctl uninstall --purge -y
kubectl delete namespace istio-system
kubectl delete gatewayclass istio istio-remote
```

To uninstall the Gateway API and Gateway API Inference Extension CRDs, see the [CRD installation guide](./install-crds.md#uninstalling-gateway-api-crds).

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
kubectl get pods -n istio-system
kubectl logs -n istio-system deployment/istiod --tail=20
```

Verify Istio was installed with the inference extension flag enabled.

### HTTPRoute not accepted

```bash
kubectl describe httproute ${GUIDE_NAME} -n ${NAMESPACE}
```

Verify that `parentRefs` matches the Gateway name and `backendRefs` matches the InferencePool name.

### No response from Gateway IP

```bash
kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}' -n ${NAMESPACE}
```

If the address is empty, your Gateway may still be waiting for a LoadBalancer service. Check that your cluster supports external load balancers.
