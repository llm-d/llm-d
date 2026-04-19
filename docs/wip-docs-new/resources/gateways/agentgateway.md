# Agentgateway

This guide shows how to deploy llm-d with
[agentgateway](https://agentgateway.dev/) as your inference gateway. By the
end, inference requests will flow from an agentgateway-managed `Gateway` to
your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with
> [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

* A Kubernetes cluster running one of the three most recent
  [Kubernetes releases](https://kubernetes.io/releases/)
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

## Step 3: Deploy Model Servers

Deploy two replicas of vLLM running `openai/gpt-oss-20b`:

> [!NOTE]
> This example uses NVIDIA GPUs. For CPU testing, use the vLLM Simulator
> (`ghcr.io/llm-d/llm-d-inference-sim:latest`).

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

## Step 4: Deploy the Gateway

Create a `Gateway` resource. agentgateway watches this resource and provisions a
proxy that accepts incoming traffic.

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: llm-d-inference-gateway
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: default
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: All
EOF
```

Verify the `Gateway` is programmed:

```bash
kubectl get gateway llm-d-inference-gateway
```

Expected output:

```text
NAME                      CLASS          ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   agentgateway   10.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 5: Deploy the InferencePool and EPP

Deploy the `InferencePool` and EPP with the Helm chart. For the current
self-installed agentgateway path, use `provider.name=none`.

The self-installed agentgateway integration does not use a dedicated
`provider.name=agentgateway` mode in the upstream `inferencepool` chart. The
agentgateway control plane handles the gateway integration, while the
`InferencePool` chart deploys the EPP and model-server discovery configuration.

```bash
IGW_CHART_VERSION=v1.4.0

helm upgrade --install llm-d-infpool \
  --dependency-update \
  --set inferencePool.modelServers.matchLabels.app=my-model \
  --set provider.name=none \
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
this setup. agentgateway manages the gateway proxy separately.

## Step 6: Configure the HTTPRoute

Create an `HTTPRoute` to connect the `Gateway` to the `InferencePool`. When
traffic reaches the `Gateway` with this route, the proxy consults the EPP and
forwards the request to the selected pod.

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
      timeouts:
        backendRequest: 0s
        request: 0s
EOF
```

Verify the `HTTPRoute` is accepted:

```bash
kubectl get httproute llm-d-route -o yaml | grep -A5 "conditions:"
```

Both `Accepted` and `ResolvedRefs` conditions should show `status: "True"`.

## Step 7: Send a Request

Get the `Gateway` external address:

```bash
export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request through the agentgateway-managed `Gateway`:

```bash
curl -s "http://${GATEWAY_IP}/v1/chat/completions" \
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
helm uninstall agentgateway -n agentgateway-system
helm uninstall agentgateway-crds -n agentgateway-system
kubectl delete namespace agentgateway-system
```

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway
kubectl get pods -n agentgateway-system
kubectl logs -n agentgateway-system deployment/agentgateway --tail=20
```

Verify the `agentgateway` `GatewayClass` is present and accepted:

```bash
kubectl get gatewayclass agentgateway
```

### InferencePool or EPP not becoming ready

```bash
kubectl get pods,inferencepool
kubectl describe inferencepool llm-d-infpool
kubectl logs deploy/llm-d-infpool-epp --tail=20
```

Confirm the model server pods match the labels used by the `InferencePool`:

```bash
kubectl get pods -l app=my-model --show-labels
```

### Model servers taking a long time to start on a fresh cluster

On fresh GPU nodes, the first model startup can take several minutes while the
CUDA and model server images are pulled. If the `Gateway` and EPP are ready but
requests are not completing yet, check the model server pod status:

```bash
kubectl get pods -l app=my-model
kubectl describe pod -l app=my-model
```
