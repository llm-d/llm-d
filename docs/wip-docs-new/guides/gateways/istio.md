# Istio

This guide shows how to deploy llm-d with [Istio](https://istio.io/) as your [Gateway API](https://gateway-api.sigs.k8s.io/) provider. By the end, inference requests will flow from an Istio-managed Gateway to your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with Gateway API and llm-d.

## Prerequisites

* A Kubernetes cluster running one of the three most recent [Kubernetes releases](https://kubernetes.io/releases/)
* [Helm](https://helm.sh/docs/intro/install/)
* [jq](https://jqlang.org/download/)
* Gateway API Inference Extension CRDs installed:

```bash
kubectl apply -k https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd
```

## Step 1: Install Istio

> [!NOTE]
> Istio v1.28.0 or later is required for full Gateway API Inference Extension support.

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

## Step 2: Deploy Model Servers

Deploy two replicas of vLLM running `openai/gpt-oss-20b`:

> [!NOTE]
> This example uses NVIDIA GPUs. For CPU testing, use the vLLM Simulator (`ghcr.io/llm-d/llm-d-inference-sim:latest`).

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
      app: my-model
  template:
    metadata:
      labels:
        app: my-model
        inference.networking.k8s.io/engine-type: vllm
    spec:
      containers:
        - name: vllm
          image: "vllm/vllm-openai:latest"
          imagePullPolicy: Always
          command: ["vllm", "serve", "openai/gpt-oss-20b"]
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

Verify the pods are running:

```bash
kubectl get pods -l app=my-model
```

## Step 3: Deploy the Gateway

Create a `Gateway` resource. Istio watches this resource and creates an Envoy-based proxy that accepts incoming traffic.

```bash
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

## Step 4: Deploy the InferencePool and EPP

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

## Step 5: Configure the HTTPRoute

Create an `HTTPRoute` to connect the Gateway to the `InferencePool`. When traffic reaches the Gateway with this route, the Proxy will consult the EPP and forward the request to the selected pod.

```bash
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

## Step 6: Send a Request

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

```bash
kubectl delete httproute llm-d-route
helm uninstall llm-d-infpool
kubectl delete gateway llm-d-inference-gateway
kubectl delete deployment my-model
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

## Further Reading

- [Istio documentation](https://istio.io/latest/docs/)
- [Gateway API documentation](https://gateway-api.sigs.k8s.io/)
- [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/)
- [Compatible gateway implementations](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/)
- [Proxy architecture](../../architecture/core/proxy.md): how standalone and gateway modes compare
- [InferencePool](../../architecture/core/inferencepool.md): the backend resource referenced by HTTPRoute
