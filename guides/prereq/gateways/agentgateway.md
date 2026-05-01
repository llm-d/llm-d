# Agentgateway

This guide shows how to deploy llm-d with
[agentgateway](https://agentgateway.dev/) as your inference gateway. By the
end, inference requests will flow from an agentgateway-managed `Gateway` to
your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. The environment variables `${GUIDE_NAME}`, `${MODEL_NAME}` and `${NAMESPACE}` should be set as part of deploying one of the well-lit path guides.
2. A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
3. [Helm](https://helm.sh/docs/intro/install/)
4. [jq](https://jqlang.org/download/)

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required Gateway API and Gateway API Inference Extension CRDs:

```bash
GATEWAY_API_VERSION=v1.5.1
GAIE_VERSION=v1.5.0

kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=${GATEWAY_API_VERSION}"
kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

Verify the APIs are available:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

## Step 2: Install Agentgateway

Install the agentgateway CRDs and control plane with inference extension support
enabled:

```bash
AGENTGATEWAY_VERSION=v1.0.0

helm upgrade --install agentgateway-crds \
  oci://cr.agentgateway.dev/charts/agentgateway-crds \
  --namespace agentgateway-system \
  --create-namespace \
  --version ${AGENTGATEWAY_VERSION}

helm upgrade --install agentgateway \
  oci://cr.agentgateway.dev/charts/agentgateway \
  --namespace agentgateway-system \
  --create-namespace \
  --version ${AGENTGATEWAY_VERSION} \
  --set inferenceExtension.enabled=true
```

Verify the installation:

```bash
kubectl get pods -n agentgateway-system
kubectl get gatewayclass agentgateway
```

Expected output:

```text
NAME           CONTROLLER                      ACCEPTED   AGE
agentgateway   agentgateway.dev/agentgateway   True       30s
```

## Step 3: Deploy the Gateway

### Agentgateway

This deploys a gateway suitable for `agentgateway`, using the `agentgateway` gateway class. This is the preferred self-installed inference gateway recipe in llm-d.

```bash
kubectl apply -k ./guides/recipes/gateway/agentgateway -n ${NAMESPACE}
```

### Agentgateway (OpenShift)

This deploys the preferred OpenShift-oriented `agentgateway`
recipe. The rendered `Gateway` uses the `agentgateway` GatewayClass and an
OpenShift-oriented `AgentgatewayParameters` resource.

```bash
kubectl apply -k ./guides/recipes/gateway/agentgateway-openshift -n ${NAMESPACE}
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Expected output:

```text
NAME                      CLASS          ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   agentgateway   10.xx.xx.xx     True         30s
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
    -H 'X-Gateway-Base-Model-Name: '"$GUIDE_NAME"'' \
    -d '{
        "model": '\"${MODEL_NAME}\"',
        "prompt": "How are you today?"
    }' | jq
```


## Cleanup

```bash
kubectl delete gateway llm-d-inference-gateway -n ${NAMESPACE}
helm uninstall agentgateway -n agentgateway-system
helm uninstall agentgateway-crds -n agentgateway-system
kubectl delete namespace agentgateway-system
kubectl delete gatewayclass agentgateway
kubectl delete -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=${GATEWAY_API_VERSION}"
kubectl delete -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
```

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
kubectl get pods -n agentgateway-system
kubectl logs -n agentgateway-system deployment/agentgateway --tail=20
```

Verify the `agentgateway` `GatewayClass` is present and accepted:

```bash
kubectl get gatewayclass agentgateway
```

### HTTPRoute not accepted

```bash
kubectl describe httproute ${GUIDE_NAME} -n ${NAMESPACE}
```

Verify that `parentRefs` matches the Gateway name and `backendRefs` matches the InferencePool name.

### No response from Gateway IP

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}'
```

If the address is empty, your Gateway may still be waiting for a LoadBalancer service. Check that your cluster supports external load balancers.
