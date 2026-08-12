# Router Recipes

llm-d uses the **llm-d Router** to make intelligent request routing decisions for inference requests. There are two deployment modes:

## Prerequisites

The commands below assume the following environment variables are set:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh   # sets ROUTER_CHART_VERSION, ROUTER_STANDALONE_CHART, ROUTER_GATEWAY_CHART
export NAMESPACE=<your-namespace>   # not set by env.sh, pick one for this install
```

## Standalone (Default)

Use this when you **do not** want to deploy a proxy via Kubernetes Gateway APIs. The standalone chart deploys the **llm-d Router** with a sidecar proxy, either **Envoy** (default) or **agentgateway**, to proxy the traffic directly.

**Chart:** `${ROUTER_STANDALONE_CHART}` (set by [`guides/env.sh`](../../env.sh))

### Standalone with Envoy (default)

```bash
helm install <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

### Standalone with agentgateway

agentgateway can be used as the sidecar proxy in place of Envoy. In this mode agentgateway runs alongside the EPP in the same pod and talks to it over localhost via ext-proc, so no Kubernetes Gateway API infrastructure is needed.

> [!NOTE]
> When using `proxyType=agentgateway`, set `router.inferencePool.create=false`.
> agentgateway creates a pseudo service for model workloads on its own, so no
> explicit service name is required. It also talks to EPP over plaintext gRPC
> on localhost, so `router.epp.flags.secure-serving=false` is required.
> `secure-serving=true` is not supported with `proxyType=agentgateway`.

```bash
helm install <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/agentgateway-proxy.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set router.proxy.proxyType=agentgateway \
  --set router.inferencePool.create=false \
  --set router.epp.flags.secure-serving=false \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

## With Kubernetes Gateway API

Use this when you want to route traffic through a proxy managed by the Kubernetes Gateway API (e.g., GKE Gateway, Istio, Agentgateway, Envoy AI Gateway). This requires:

1. A Gateway control plane installed (see [prereq/gateway-provider](../../../docs/infrastructure/gateway/README.md))
2. Creating a Gateway resource (see [recipes/gateway](../gateway/))
3. Deploying the inferencepool chart (below)

**Chart:** `${ROUTER_GATEWAY_CHART}` (set by [`guides/env.sh`](../../env.sh))

```bash
helm install <release-name> \
  ${ROUTER_GATEWAY_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

## Enable Prometheus Monitoring (Optional)

The Router's monitoring values file (`features/monitoring.values.yaml`) renders a
`ServiceMonitor` CR, which requires the **Prometheus Operator** CRDs to be present in the
cluster. On a fresh cluster without those CRDs, layering this file onto the initial install
will fail Helm validation.

To enable monitoring, deploy the monitoring stack first (see the
[Observability guide](../../../docs/operations/observability/setup.md)) and then layer the
monitoring values file on top of an existing release:

```bash
helm upgrade <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set provider.name=<gke|istio|none> \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

If your cluster already has the Prometheus Operator CRDs installed, you can also add
`-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` to the initial
`helm install` command above instead.

## Calibration

[`calibration/`](calibration/) provides a reusable tool to measure
`peakPrefillThroughput` for the `prefix-cache-affinity-filter` plugin on your own
hardware/model. See its [README](calibration/README.md).

## Values Layering

Both modes share a common `base.values.yaml` containing the router image, ports, and common pod selector labels. Feature values (monitoring, tracing) and guide-specific values are layered on top:

```
base.values.yaml                              # shared defaults (this directory)
  + features/monitoring.values.yaml           # optional feature toggles
  + features/agentgateway-proxy.values.yaml   # required for agentgateway config args
  + <guide>/router/<guide>.values.yaml     # guide-specific overrides
```
