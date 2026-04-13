# Istio

This guide shows how to deploy llm-d with [Istio](https://istio.io/) as your [Gateway API](https://gateway-api.sigs.k8s.io/) provider. By the end, inference requests will flow through an Istio-managed Gateway to your model servers through the llm-d EPP.

> [!NOTE]
> This guide assumes you have already deployed model servers and are familiar with the llm-d [standalone quickstart](../../getting-started/quickstart.md). If you are new to llm-d, start there first.

## Why Gateway API?

The standalone quickstart runs the Envoy proxy and EPP together in a single pod. That is a good way to get started, but it is not how most production deployments are structured.

[Gateway API](https://gateway-api.sigs.k8s.io/) is the Kubernetes-standard way to manage L4 and L7 traffic. When you use it with the [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/), a gateway such as Istio can send each inference request to the EPP for scheduling while still giving you the networking features you would expect in production, such as TLS termination, traffic splitting, and access control.

```text
                          Standalone
                          ─────────
  Client ──► [ Envoy + EPP (same pod) ] ──► vLLM Pods


                          Istio Gateway
                          ─────────────
  Client ──► [ Istio Gateway ] ──► [ EPP ] ──► vLLM Pods
                  │                    │
                  │  ext-proc call     │
                  └────────────────────┘
```

The request flow stays the same in both modes: the proxy asks the EPP where to send the request, then forwards it to the best model server. In this setup, Istio manages the gateway, TLS termination, and traffic policies for you.

## Prerequisites

- A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
- [Helm](https://helm.sh/docs/intro/install/)
- [jq](https://jqlang.org/download/)
- Gateway API Inference Extension CRDs installed:

  ```bash
  kubectl apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd
  ```

- Model servers deployed and labeled (see [quickstart](../../getting-started/quickstart.md))

> [!NOTE]
> Istio v1.28.0 or later is required for full Gateway API Inference Extension support.

## Step 1: Install Istio

Download and install Istio with the Gateway API Inference Extension flag enabled:

```bash
ISTIO_VERSION=1.28.0
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

## Step 2: Deploy the Gateway

Create a `Gateway` resource. Istio watches this resource and automatically creates the Envoy-based gateway proxy that receives incoming traffic.

```yaml
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: llm-d-inference-gateway
spec:
  gatewayClassName: istio
  listeners:
    - name: http
      protocol: HTTP
      port: 80
EOF
```

Verify the Gateway is accepted:

```bash
kubectl get gateway llm-d-inference-gateway
```

Expected output:

```text
NAME                      CLASS   ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   istio   10.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 3: Deploy the InferencePool and EPP

Next, deploy the `InferencePool` and EPP with the Helm chart, using `provider.name=istio`. This configures the EPP to work with an Istio-managed Gateway instead of the standalone proxy used in the quickstart.

```bash
IGW_CHART_VERSION=v1.4.0

helm install llm-d-infpool \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=my-model \
  --set provider.name=istio \
  --version ${IGW_CHART_VERSION} \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

> [!NOTE]
> Compared with the standalone quickstart, the main difference here is the Helm chart and provider setting: use `charts/inferencepool` and set `provider.name=istio`.

Verify the EPP is running and the InferencePool is created:

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

## Step 4: Configure the HTTPRoute

Create an `HTTPRoute` to connect the Gateway to the `InferencePool`. When traffic reaches the Gateway, this route sends the request to the `InferencePool`, where the EPP chooses the best model server.

```yaml
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-d-route
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: llm-d-inference-gateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: llm-d-infpool
          port: 8000
EOF
```

Verify the HTTPRoute is accepted:

```bash
kubectl get httproute llm-d-route -o yaml | grep -A5 "conditions:"
```

Both `Accepted` and `ResolvedRefs` conditions should show `status: "True"`.

## Step 5: Send a Request

Get the Gateway's external address:

```bash
export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request through the Istio Gateway:

```bash
curl -s http://${GATEWAY_IP}/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "openai/gpt-oss-20b",
    "messages": [{"role": "user", "content": "Hello, who are you?"}],
    "max_tokens": 50
  }'
```

Expected output:

```json
{
  "id": "chatcmpl-...",
  "model": "openai/gpt-oss-20b",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "message": {
        "role": "assistant",
        "content": "..."
      }
    }
  ]
}
```

## Cleanup

If you are using a test cluster, you can remove the resources created in this guide with the following commands:

```bash
kubectl delete httproute llm-d-route
helm uninstall llm-d-infpool
kubectl delete gateway llm-d-inference-gateway
istioctl uninstall --purge -y
kubectl delete namespace istio-system
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

- InferencePool not created: check `kubectl get inferencepool`
- CRDs not installed: check `kubectl get crd | grep inference`

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

## Further Reading

- [Istio documentation](https://istio.io/latest/docs/)
- [Gateway API documentation](https://gateway-api.sigs.k8s.io/)
- [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/)
- [Compatible gateway implementations](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/)
- [Proxy architecture](../../architecture/core/proxy.md): how standalone and gateway modes compare
- [InferencePool](../../architecture/core/inferencepool.md): the backend resource referenced by HTTPRoute
