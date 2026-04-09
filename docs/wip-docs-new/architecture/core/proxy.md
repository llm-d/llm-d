# Proxy - rob

The proxy is the entry point for inference requests in llm-d, receiving client traffic and routing it to the optimal model server via the EPP.

## Functionality

llm-d leverages Envoy's [External Processing](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) to extend production proxies into "inference-aware" proxies by offloading request scheduling to the llm-d EPP. This enables llm-d to reuse the rich existing ecosystem of high-performance, production-quality proxy technologies in the Kubernetes ecosystem.

The proxy's job is to:

- **Accept incoming inference requests** from clients (OpenAI-compatible API)
- **Consult the EPP** via ext-proc to determine the optimal backend endpoint
- **Route the request** to the selected model server pod
- **Stream responses** back to the client

## Design

llm-d provides two deployment patterns for the proxy:

### Gateway API

[Gateway API](https://gateway-api.sigs.k8s.io/) is an official Kubernetes project focused on L4 and L7 routing in Kubernetes, representing the next generation of Kubernetes Ingress, Load Balancing, and Service Mesh APIs.

The [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) leverages Envoy's External Processing to extend any gateway that supports both ext-proc and Gateway API into an inference gateway. This extends popular gateways like Envoy Gateway, Istio, kgateway, and GKE Gateway to become Inference Gateways -- supporting inference platform teams self-hosting Generative Models on Kubernetes.

This integration makes it easy to expose and control access to your local OpenAI-compatible chat completion endpoints to other workloads on or off cluster, or to integrate your self-hosted models alongside model-as-a-service providers in a higher level AI Gateways like LiteLLM, Gloo AI Gateway, or Apigee.

The architecture:

--> XXX Insert Architecture Diagram

Gateway API deployments require the Gateway implementation to support Gateway API Inference Extension (GAIE). Compatible implementations include [Istio](https://istio.io/), [kgateway](https://kgateway.io/), and [GKE Gateway](https://cloud.google.com/kubernetes-engine/docs/concepts/gateway-api). A full list can be found [here](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/).

> Gateway API based deployments are recommended for online production services.

### Standalone

The standalone mode deploys an Envoy proxy as a sidecar to the EPP, offering a lightweight, flexible deployment pattern without requiring Gateway API infrastructure.

In standalone mode:

- Envoy runs alongside the EPP in the same pod
- ext-proc communication happens over localhost
- No Gateway, HTTPRoute, or gateway controller is needed
- Traffic is sent directly to the EPP pod's externally exposed port

--> XXX Insert Architecture Diagram

> Standalone deployments are intended for workloads where the machinery of Gateway API creates too much operational overhead -- such as clusters using Ingress, basic testing and evaluations, batch inference, and RL post-training.

### Request Flow (Both Modes)

Regardless of the deployment pattern, the request flow is the same:

1. Client sends an inference request to the proxy
2. The proxy's ext-proc filter calls the EPP
3. The EPP evaluates endpoints using its plugin pipeline (handlers, filters, scorers, picker)
4. The EPP returns the selected endpoint address
5. The proxy routes the request to that model server pod
6. The model server streams the response back through the proxy to the client

## Configuration

### Gateway API

#### Prerequisites

- A Gateway API implementation that supports ext-proc (Istio, kgateway, GKE Gateway, etc.)
- Gateway API CRDs installed on the cluster
- Gateway API Inference Extension CRDs installed

#### Gateway Resource

Deploy a Gateway resource for your chosen implementation:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: llm-d-inference-gateway
spec:
  gatewayClassName: <your-gateway-class>  # e.g., istio, kgateway, gke-l7-rilb
  listeners:
    - name: http
      protocol: HTTP
      port: 80
```

#### HTTPRoute

Route traffic to the InferencePool:

```yaml
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
    - backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: llm-d-infpool
          port: 8000
      matches:
        - path:
            type: PathPrefix
            value: /
```

#### Provider Selection

When deploying the InferencePool Helm chart, set the provider to match your gateway implementation:

| Provider | `provider.name` | Notes |
|----------|-----------------|-------|
| GKE Gateway | `gke` | Google Kubernetes Engine managed gateway |
| Istio | `istio` | Istio service mesh gateway |
| Agentgateway / kgateway | `none` | Used for both agentgateway and legacy kgateway |

### Standalone

The standalone proxy is deployed automatically when using the standalone deployment path. It does not require Gateway API resources.

Key configuration is handled through Helm values:

| Field | Description | Example |
|-------|-------------|---------|
| `inferenceExtension.extProcPort` | Port the EPP listens on for ext-proc traffic | `9002` |

## Examples

### Gateway API with Istio

```yaml
# Gateway
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
---
# HTTPRoute
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
    - backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: llm-d-infpool
          port: 8000
      matches:
        - path:
            type: PathPrefix
            value: /
```

Install the InferencePool with Istio provider:

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=istio" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

### Standalone Deployment

Install the InferencePool without a gateway provider:

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=none" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

Send requests directly to the EPP pod:

```bash
curl http://<epp-service>:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Further Reading

- [Gateway API documentation](https://gateway-api.sigs.k8s.io/)
- [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/)
- [Compatible gateway implementations](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/)
- [EPP](epp.md) -- the scheduling extension that powers inference-aware routing
- [InferencePool](inferencepool.md) -- the backend resource referenced by HTTPRoute
