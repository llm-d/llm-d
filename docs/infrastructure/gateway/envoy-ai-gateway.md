# Envoy AI Gateway

This guide shows how to deploy llm-d with
[Envoy AI Gateway](https://aigateway.envoyproxy.io/) (EAIG) as your inference
gateway. By the end, inference requests will flow from an EAIG-managed `Gateway`
to your model servers via the llm-d EPP.

Envoy AI Gateway is an open source project that extends
[Envoy Gateway](https://github.com/envoyproxy/gateway) to handle request traffic
to Generative AI services. It supports both egress to external AI providers and
ingress to self-hosted model servers, the latter through the
[Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/).
For the llm-d use case, EAIG acts as a Gateway API implementation whose
`InferencePool` integration consults the llm-d Endpoint Picker (EPP) to load
balance LLM traffic across model server replicas.

> [!NOTE]
> This guide assumes familiarity with
> [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. The environment variables `${GUIDE_NAME}`, `${MODEL_NAME}` and `${NAMESPACE}`
   should be set as part of deploying one of the well-lit path guides.
2. A Kubernetes cluster running one of the three most recent
   [Kubernetes releases](https://kubernetes.io/releases/).
3. [Helm](https://helm.sh/docs/intro/install/).
4. [jq](https://jqlang.org/download/).
5. [kubectl](https://kubernetes.io/docs/tasks/tools/).

> [!IMPORTANT]
> Envoy AI Gateway is built on top of [Envoy Gateway](https://github.com/envoyproxy/gateway),
> which provides the actual `GatewayClass` controller and Envoy-based data
> plane. If Envoy Gateway is already installed in your cluster, uninstall it
> first so that the EAIG-specific configuration below is applied cleanly:
>
> ```bash
> helm uninstall eg -n envoy-gateway-system
> kubectl delete namespace envoy-gateway-system
> ```

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required CRDs by following the
[CRD installation guide](./install-crds.md). EAIG requires the Gateway API
CRDs and the Gateway API Inference Extension CRDs that define `InferencePool`.
(The Envoy Gateway Backend API that EAIG relies on is a control-plane feature
enabled via the Helm values in Step 2, not a separate CRD install here.)

## Step 2: Install Envoy Gateway with the AI Gateway configuration

Install Envoy Gateway using the AI Gateway-specific Helm values. This values
file enables the Envoy Gateway extension server that EAIG relies on, plus the
Backend API and Envoy Patch Policy support.

```bash
EG_VERSION=v0.0.0-latest  # Use 'v0.0.0-latest' for latest, or a release tag like 'v1.8.1'
AIGW_VERSION=v1.0.0        # Pin to a stable EAIG release tag, e.g. v1.0.0

helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version ${EG_VERSION} \
  --namespace envoy-gateway-system \
  --create-namespace \
  -f https://raw.githubusercontent.com/envoyproxy/ai-gateway/${AIGW_VERSION}/manifests/envoy-gateway-values.yaml \
  -f https://raw.githubusercontent.com/envoyproxy/ai-gateway/${AIGW_VERSION}/examples/inference-pool/envoy-gateway-values-addon.yaml
```

The second `-f` file is the InferencePool addon: it tells Envoy Gateway to
reconcile `InferencePool` resources from the
`inference.networking.k8s.io` group, which is required to route to an
`InferencePool`.

Wait for the deployment to become available:

```bash
kubectl wait --timeout=2m -n envoy-gateway-system deployment/envoy-gateway \
  --for=condition=Available
```

## Step 3: Install Envoy AI Gateway

Install the EAIG CRDs first, then the controller.

```bash
helm upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm \
  --version ${AIGW_VERSION} \
  --namespace envoy-ai-gateway-system \
  --create-namespace

helm upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm \
  --version ${AIGW_VERSION} \
  --namespace envoy-ai-gateway-system \
  --create-namespace

kubectl wait --timeout=2m -n envoy-ai-gateway-system deployment/ai-gateway-controller \
  --for=condition=Available
```

Verify the installation:

```bash
kubectl get pods -n envoy-ai-gateway-system
```

Expected output:

```text
NAME                                      READY   STATUS    RESTARTS   AGE
ai-gateway-controller-xxxxxxxxxx-xxxxx    1/1     Running   0          30s
```

> [!NOTE]
> The `ai-gateway-controller` pod runs only the controller. The ext-proc
> container that performs AI traffic translation is injected as a sidecar into
> the Envoy data-plane pods that Envoy Gateway creates for your `Gateway`, so
> it will not appear under `envoy-ai-gateway-system`. Once the `Gateway` is
> programmed (Step 4), the Envoy proxy pods in `envoy-gateway-system` will show
> two containers (envoy + ext-proc).

> [!NOTE]
> On `main`, the chart version `v0.0.0-latest` is unstable and the container
> tags are overwritten over time. Pin `AIGW_VERSION` to a stable release tag
> (for example `v1.0.0`) for production deployments.

## Step 4: Deploy the Gateway

Envoy AI Gateway uses Envoy Gateway's `GatewayClass` controller
(`gateway.envoyproxy.io/gatewayclass-controller`). Create a `GatewayClass` and
a `Gateway` in your namespace:

```bash
kubectl apply -n ${NAMESPACE} -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: envoy-ai-gateway
spec:
  controllerName: gateway.envoyproxy.io/gatewayclass-controller
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: llm-d-inference-gateway
spec:
  gatewayClassName: envoy-ai-gateway
  listeners:
    - name: http
      protocol: HTTP
      port: 80
EOF
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Expected output:

```text
NAME                      CLASS               ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   envoy-ai-gateway    xx.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 5: Send a Request

> [!IMPORTANT]
> Before sending requests, you must deploy a well-lit path guide. This sets
> up a model server deployment, an `InferencePool`, and an `HTTPRoute` to
> connect the Gateway to the pool.

Get the `Gateway` external address:

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} \
  -o jsonpath='{.status.addresses[0].value}')
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
kubectl delete gatewayclass envoy-ai-gateway
helm uninstall aieg -n envoy-ai-gateway-system
helm uninstall aieg-crd -n envoy-ai-gateway-system
kubectl delete namespace envoy-ai-gateway-system
helm uninstall eg -n envoy-gateway-system
kubectl delete namespace envoy-gateway-system
```

To uninstall the Gateway API and Gateway API Inference Extension CRDs, see the
[CRD installation guide](./install-crds.md#uninstalling-gateway-api-crds).

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
kubectl get pods -n envoy-gateway-system
kubectl logs -n envoy-gateway-system deployment/envoy-gateway --tail=20
kubectl get pods -n envoy-ai-gateway-system
kubectl logs -n envoy-ai-gateway-system deployment/ai-gateway-controller --tail=20
```

Verify that Envoy Gateway was installed with both the AI Gateway values file
and the InferencePool addon file (`-f` flags in Step 2), and that the
`ai-gateway-controller` pod is `Ready`.

### HTTPRoute not accepted

```bash
kubectl describe httproute ${GUIDE_NAME} -n ${NAMESPACE}
```

Verify that `parentRefs` matches the Gateway name (`llm-d-inference-gateway`)
and `backendRefs` matches the `InferencePool` name. When routing to an
`InferencePool`, the Envoy Gateway InferencePool addon (Step 2) must be enabled
or the backend reference will not resolve.

### No response from Gateway IP

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} \
  -o jsonpath='{.status.addresses[0].value}'
```

If the address is empty, your Gateway may still be waiting for a LoadBalancer
service. Check that your cluster supports external load balancers, or
port-forward the Envoy service instead:

```bash
ENVOY_SVC=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=llm-d-inference-gateway \
  -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n envoy-gateway-system svc/${ENVOY_SVC} 8080:80
export IP=127.0.0.1:8080
```

### Getting `fault filter abort` response

A couple of issues may cause this:

1. The request doesn't match the routing rules set up on the HTTPRoute.
2. A misconfiguration in the gateway's backend routing. When configured
   correctly, the HTTPRoute status should have a condition of type `Reconciled`
   and reason `ReconciliationSucceeded`.
