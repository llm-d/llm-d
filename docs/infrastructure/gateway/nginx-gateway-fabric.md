# NGINX Gateway Fabric

This guide shows how to deploy llm-d with
[NGINX Gateway Fabric](https://docs.nginx.com/nginx-gateway-fabric/) as your inference gateway. By the
end, inference requests will flow from an NGINX-managed `Gateway` to
your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. The environment variables `${GUIDE_NAME}`, `${MODEL_NAME}` and `${NAMESPACE}` should be set as part of deploying one of the well-lit path guides.
2. A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
3. [Helm](https://helm.sh/docs/intro/install/)
4. [jq](https://jqlang.org/download/)

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

Install the required CRDs by following the [CRD installation guide](./install-crds.md).

## Step 2: Install NGINX Gateway Fabric

Install NGINX Gateway Fabric with the Gateway API Inference Extension enabled:

```bash
helm install ngf oci://ghcr.io/nginx/charts/nginx-gateway-fabric \
  --create-namespace \
  --namespace nginx-gateway \
  --set nginxGateway.gwAPIInferenceExtension.enable=true \
  --wait
```

If you prefer to pin the [version of your NGINX Gateway Fabric](https://github.com/nginx/nginx-gateway-fabric) deployment you can set the version as a variable and use it with ```--version``` during helm install:

```bash
NGF_VERSION=2.7.2
```

Install NGINX Gateway Fabric with the Gateway API Inference Extension enabled:

```bash
helm install ngf oci://ghcr.io/nginx/charts/nginx-gateway-fabric \
  --version ${NGF_VERSION} \
  --create-namespace \
  --namespace nginx-gateway \
  --set nginxGateway.gwAPIInferenceExtension.enable=true \
  --wait
```


Verify the installation:

```bash
kubectl get pods -n nginx-gateway
kubectl get gatewayclass nginx
```

Expected output:

```text
NAME    CONTROLLER                                   ACCEPTED   AGE
nginx   gateway.nginx.org/nginx-gateway-controller   True       30s
```

## Step 3: Deploy the Gateway

Set the llm-d version to match your deployment:

```bash
LLM_D_VERSION=main  # Use 'main' for latest, or a release tag like 'v0.7.0'
```

Deploy a Gateway using the `nginx` GatewayClass:

```bash
kubectl apply -k "https://github.com/llm-d/llm-d/guides/recipes/gateway/nginx?ref=${LLM_D_VERSION}" -n ${NAMESPACE}
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Expected output:

```text
NAME                      CLASS   ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   nginx   10.xx.xx.xx     True         30s
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
helm uninstall ngf -n nginx-gateway
kubectl delete namespace nginx-gateway
kubectl delete -f https://raw.githubusercontent.com/nginx/nginx-gateway-fabric/main/deploy/crds.yaml
```

To uninstall the Gateway API and Gateway API Inference Extension CRDs, see the [CRD installation guide](./install-crds.md#uninstalling-gateway-api-crds).

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
kubectl logs -n nginx-gateway deployment/ngf-nginx-gateway-fabric
kubectl logs -n ${NAMESPACE} deployment/llm-d-inference-gateway-nginx

Verify the `nginx` `GatewayClass` is present and accepted:

```bash
kubectl get gatewayclass nginx
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

## See also

- [NGINX Gateway Fabric: Gateway API Inference Extension](https://docs.nginx.com/nginx-gateway-fabric/how-to/gateway-api-inference-extension/) — upstream how-to guide this document is based on.
- [NGINX Gateway Fabric installation](https://docs.nginx.com/nginx-gateway-fabric/install/helm/) — full Helm install options including NGINX Plus and advanced configuration.
