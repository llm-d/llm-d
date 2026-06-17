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

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required CRDs by following the [CRD installation guide](./install-crds.md).

## Step 2: Install Envoy AI Gateway

Install Envoy Gateway with the AI Gateway integration values, token rate limiting add-on, and InferencePool add-on:

```bash
ENVOY_GATEWAY_VERSION=v1.8.1

# Install the CRDs first, skipping the Gateway API CRDs installed in the previous step.
# 
# Note 1: We’re using helm template piped into kubectl apply instead of helm install due to aknown Helm limitation related
# to large CRDs in the templates/ directory: https://github.com/helm/helm/pull/12277
# Note 2: We filter the output of the Helm template to remove offending lines until this is fixed: https://github.com/helm/helm/pull/32217
helm template eg-crds oci://docker.io/envoyproxy/gateway-crds-helm \
  --version ${ENVOY_GATEWAY_VERSION} \
  --set crds.gatewayAPI.enabled=false \
  --set crds.envoyGateway.enabled=true \
  | grep -v '^Pulled:' | grep -v '^Digest:' | kubectl apply --server-side -f -

# Install Envoy gateway
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version ${ENVOY_GATEWAY_VERSION} \
  --namespace envoy-gateway-system \
  --create-namespace \
  --skip-crds \
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

Set the llm-d version to match your deployment:

```bash
LLM_D_VERSION=main  # Use 'main' for latest, or a release tag like 'v0.7.0'
```

This deploys a gateway suitable for `envoy-ai-gateway`, using the `envoy-ai-gateway` gateway class. This is the preferred self-installed inference gateway recipe in llm-d.

```bash
kubectl apply -k "https://github.com/llm-d/llm-d/guides/recipes/gateway/envoy-ai-gateway?ref=${LLM_D_VERSION}" -n ${NAMESPACE}
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
kubectl delete clienttrafficpolicy client-buffer-limit -n ${NAMESPACE}
kubectl delete gateway llm-d-inference-gateway -n ${NAMESPACE}
helm uninstall envoy-ai-gateway -n envoy-ai-gateway-system
helm uninstall envoy-ai-gateway-crd -n envoy-ai-gateway-system
kubectl delete namespace envoy-ai-gateway-system
helm uninstall eg -n envoy-gateway-system
kubectl delete namespace envoy-gateway-system
helm template eg-crds oci://docker.io/envoyproxy/gateway-crds-helm \
  --version ${ENVOY_GATEWAY_VERSION} \
  --set crds.gatewayAPI.enabled=false \
  --set crds.envoyGateway.enabled=true \
  | grep -v '^Pulled:' | grep -v '^Digest:' | kubectl delete -f -
kubectl delete gatewayclass envoy-ai-gateway
```

To uninstall the Gateway API and Gateway API Inference Extension CRDs, see the [CRD installation guide](./install-crds.md#uninstalling-gateway-api-crds).

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
