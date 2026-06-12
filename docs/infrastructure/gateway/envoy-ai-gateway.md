# Envoy AI Gateway

This guide shows how to deploy llm-d with
[Envoy AI Gateway](https://aigateway.envoyproxy.io/) as your inference gateway. By the
end, inference requests will flow from an Envoy-managed `Gateway` to
your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. The environment variables `${GUIDE_NAME}`, `${MODEL_NAME}` and `${NAMESPACE}` should be set as part of deploying one of the well-lit path guides.
2. A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/) (minimum Kubernetes 1.32)
3. [Helm](https://helm.sh/docs/intro/install/)
4. [jq](https://jqlang.org/download/)

## Step 1: Install Gateway API Inference Extension CRDs

Envoy Gateway's Helm chart installs Gateway API CRDs automatically. Install only the Gateway API Inference Extension CRDs:

```bash
GAIE_VERSION=v1.5.0

kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```

Verify the APIs are available:

```bash
kubectl api-resources --api-group=inference.networking.k8s.io
```

## Step 2: Install Envoy AI Gateway

Install Envoy Gateway with the AI Gateway integration values, token rate limiting add-on, and InferencePool add-on:

```bash
ENVOY_GATEWAY_VERSION=v1.8.1

helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version ${ENVOY_GATEWAY_VERSION} \
  --namespace envoy-gateway-system \
  --create-namespace \
  -f https://raw.githubusercontent.com/envoyproxy/ai-gateway/main/manifests/envoy-gateway-values.yaml \
  -f https://raw.githubusercontent.com/envoyproxy/ai-gateway/main/examples/inference-pool/envoy-gateway-values-addon.yaml

# Give permissions to Envoy Gateway to watch InferencePool resources
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: envoy-gateway-inference-access
rules:
- apiGroups:
  - inference.networking.k8s.io
  resources:
  - inferencepools
  verbs:
  - get
  - list
  - watch
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: envoy-gateway-inference-access
subjects:
- kind: ServiceAccount
  name: envoy-gateway
  namespace: envoy-gateway-system
roleRef:
  kind: ClusterRole
  name: envoy-gateway-inference-access
  apiGroup: rbac.authorization.k8s.io
EOF

kubectl wait --timeout=2m -n envoy-gateway-system deployment/envoy-gateway --for=condition=Available
```

Install the Envoy AI Gateway CRDs and controller:

```bash
ENVOY_AI_GATEWAY_VERSION=v0.7.0

helm upgrade -i envoy-ai-gateway-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm \
  --version ${ENVOY_AI_GATEWAY_VERSION} \
  --namespace envoy-ai-gateway-system \
  --create-namespace

helm upgrade -i envoy-ai-gateway oci://docker.io/envoyproxy/ai-gateway-helm \
  --version ${ENVOY_AI_GATEWAY_VERSION} \
  --namespace envoy-ai-gateway-system \
  --create-namespace

kubectl wait --timeout=2m -n envoy-ai-gateway-system deployment/ai-gateway-controller --for=condition=Available
```

Create the GatewayClass

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: envoy-ai-gateway
spec:
  controllerName: gateway.envoyproxy.io/gatewayclass-controller
EOF
```

Verify the installation:

```bash
kubectl get pods -n envoy-gateway-system
kubectl get pods -n envoy-ai-gateway-system
kubectl get gatewayclass envoy-ai-gateway
```

Expected output:

```text
NAME   CONTROLLER                                      ACCEPTED   AGE
envoy-ai-gateway   gateway.envoyproxy.io/gatewayclass-controller   True       30s
```

## Step 3: Deploy the Gateway

This deploys a gateway using the `envoy-ai-gateway` GatewayClass provided by Envoy Gateway:

```bash
kubectl apply -k ./guides/recipes/gateway/envoy-ai-gateway -n ${NAMESPACE}
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Expected output:

```text
NAME                      CLASS   ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   envoy-ai-gateway    10.xx.xx.xx     True         30s
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
helm uninstall envoy-ai-gateway -n envoy-ai-gateway-system
helm uninstall envoy-ai-gateway-crd -n envoy-ai-gateway-system
kubectl delete namespace envoy-ai-gateway-system
helm uninstall eg -n envoy-gateway-system
kubectl delete namespace envoy-gateway-system
kubectl delete gatewayclass envoy-ai-gateway
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
helm template eg oci://docker.io/envoyproxy/gateway-helm --version ${ENVOY_GATEWAY_VERSION} --include-crds | yq 'select(.kind == "CustomResourceDefinition")' | kubectl delete -f -
```

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
kubectl get pods -n envoy-gateway-system
kubectl logs -n envoy-gateway-system deployment/envoy-gateway --tail=20
```

Verify the `envoy-ai-gateway` `GatewayClass` is present and accepted:

```bash
kubectl get gatewayclass envoy-ai-gateway
```

Also check the Envoy AI Gateway controller is running:

```bash
kubectl get pods -n envoy-ai-gateway-system
kubectl logs -n envoy-ai-gateway-system deployment/ai-gateway-controller --tail=20
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
