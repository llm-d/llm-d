# Scheduler Recipes

llm-d uses the **llm-d Router** to make intelligent request routing decisions for inference requests. There are two deployment modes:


## Standalone (Default)

Use this when you **do not** want to deploy a proxy via Kubernetes Gateway APIs. The standalone chart deploys the **llm-d Router** with an Envoy sidecar to proxy the traffic directly.

**Chart:** `oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone`

```bash
helm install <release-name> \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/<your-guide>/scheduler/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version v1.4.0
```

## With Kubernetes Gateway API

Use this when you want to route traffic through a proxy managed by the Kubernetes Gateway API (e.g., GKE Gateway, Istio, Agentgateway). This requires:

1. A Gateway control plane installed (see [prereq/gateway-provider](../../prereq/gateway-provider/README.md))
2. Creating a Gateway resource (see [recipes/gateway](../gateway/))
3. Deploying the inferencepool chart (below)

**Chart:** `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool`

```bash
helm install <release-name> \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/<your-guide>/scheduler/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version v1.4.0
```

## Values Layering

Both modes share a common `base.values.yaml` containing the scheduler image, ports, and common pod selector labels. Feature values (monitoring, tracing) and guide-specific values are layered on top:

```
base.values.yaml                              # shared defaults (this directory)
  + features/monitoring.values.yaml           # optional feature toggles
  + <guide>/scheduler/<guide>.values.yaml     # guide-specific overrides
```
