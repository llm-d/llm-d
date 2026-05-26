# Router Recipes

llm-d uses the **llm-d Router** to make intelligent request routing decisions for inference requests. There are two deployment modes:

## Standalone (Default)

Use this when you **do not** want to deploy a proxy via Kubernetes Gateway APIs. The standalone chart deploys the **llm-d Router** with an Envoy sidecar to proxy the traffic directly.

**Chart:** `oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev`

```bash
helm install <release-name> \
  oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

## With Kubernetes Gateway API

Use this when you want to route traffic through a proxy managed by the Kubernetes Gateway API (e.g., GKE Gateway, Istio, Agentgateway). This requires:

1. A Gateway control plane installed (see [prereq/gateway-provider](../../prereq/gateway-provider/README.md))
2. Creating a Gateway resource (see [recipes/gateway](../gateway/))
3. Deploying the inferencepool chart (below)

**Chart:** `oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev`

```bash
helm install <release-name> \
  oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

## Values Layering

Both modes share a common `base.values.yaml` containing the router image, ports, and common pod selector labels. Feature values (monitoring, tracing) and guide-specific values are layered on top:

```
base.values.yaml                              # shared defaults (this directory)
  + features/monitoring.values.yaml           # optional feature toggles
  + <guide>/router/<guide>.values.yaml     # guide-specific overrides
```
