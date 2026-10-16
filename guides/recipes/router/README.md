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

1. A Gateway control plane installed (see [Gateway Providers](../../../docs/infrastructure/gateway/README.md))
2. Creating a Gateway resource (see [recipes/gateway](../gateway/))
3. Deploying the inferencepool chart (below)

**Chart:** `${ROUTER_GATEWAY_CHART}` (set by [`guides/env.sh`](../../env.sh))

```bash
helm install <release-name> \
  ${ROUTER_GATEWAY_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  --set provider.name=<none|gke|agentgateway|istio> \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

`provider.name` selects gateway-provider-specific resources in the chart: `gke` renders a
`HealthCheckPolicy` (required on GKE; without it the load balancer health check fails against
vLLM and requests return 503), `istio` renders a `DestinationRule`, and `agentgateway` and
`none` render no provider-specific resources.

## Enable Prometheus Monitoring (Optional)

To enable monitoring, set up the [monitoring stack](../../../docs/operations/observability/setup.md) first, then layer the monitoring values file for your platform on top of an existing release (or add `-f` to the initial `helm install` command):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

`features/monitoring.values.yaml` renders a `ServiceMonitor` (`monitoring.coreos.com/v1`), which requires the **Prometheus Operator** CRDs in the cluster:

```bash
helm upgrade <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

`features/monitoring-gke.values.yaml` renders a GMP `PodMonitoring` (`monitoring.googleapis.com/v1`), a metrics-reader `ServiceAccount` and token `Secret`, and the RBAC bindings required for the GMP collector to scrape the EPP (add `--set router.monitoring.provider.gmp.autopilot=true` on GKE Autopilot):

```bash
helm upgrade <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

</details>
<!-- tabs:end -->

> [!NOTE]
> `helm upgrade` does not inherit the previous release's values. Re-supply every `-f` and
> `--set` the original install used, or the upgrade reverts them to chart defaults. For an
> agentgateway release that means adding
> `-f ${REPO_ROOT}/guides/recipes/router/features/agentgateway-proxy.values.yaml` and the
> `router.proxy.proxyType`, `router.inferencePool.create` and
> `router.epp.flags.secure-serving` flags to the command above.

## Enable Distributed Tracing (Optional)

To export OpenTelemetry spans from the EPP, set up your tracing collector (see [Trace Requests](../../../docs/operations/observability/tracing.md)) and layer the tracing values file for your platform onto the router release (pass `--set router.tracing.sampling.samplerArg="1.0"` when verifying with a small number of test requests):

<!-- tabs:start group=tracing -->
<details open>
<summary><b>Jaeger (OSS / OpenShift)</b></summary>

[`features/tracing.values.yaml`](features/tracing.values.yaml) exports traces to `http://otel-collector:4317` with a 5% sampling ratio (`samplerArg: "0.05"`):

```bash
helm upgrade <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/tracing.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

</details>
<details>
<summary><b>GKE (Google Cloud Trace)</b></summary>

[`features/tracing-gke.values.yaml`](features/tracing-gke.values.yaml) exports traces to the [GKE Managed OpenTelemetry](../../../docs/infrastructure/providers/gke/README.md#monitoring--telemetry) collector (`http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4317`) with a 5% sampling ratio (`samplerArg: "0.05"`). If using the self-managed fallback collector (`install-otel-collector-jaeger.sh --gke`), layer [`features/tracing.values.yaml`](features/tracing.values.yaml) (`http://otel-collector:4317`) instead:

```bash
helm upgrade <release-name> \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/tracing-gke.values.yaml \
  -f ${REPO_ROOT}/guides/<your-guide>/router/<your-guide>.values.yaml \
  -n ${NAMESPACE} \
  --version ${ROUTER_CHART_VERSION}
```

</details>
<!-- tabs:end -->

## Calibration

[`calibration/`](calibration/) provides a reusable tool to measure
`peakPrefillThroughput` for the `prefix-cache-affinity-filter` plugin on your own
hardware/model. See its [README](calibration/README.md).

## Values Layering

Both modes share a common `base.values.yaml` containing the router image, ports, and common pod selector labels. Feature values (monitoring, tracing) and guide-specific values are layered on top:

```text
base.values.yaml                              # shared defaults (this directory)
  + features/monitoring.values.yaml           # optional Prometheus Operator ServiceMonitor
  + features/monitoring-gke.values.yaml       # optional GKE Managed Prometheus PodMonitoring
  + features/tracing.values.yaml              # optional OpenTelemetry distributed tracing (Jaeger)
  + features/tracing-gke.values.yaml          # optional GKE Managed OpenTelemetry tracing (Cloud Trace)
  + features/agentgateway-proxy.values.yaml   # required for agentgateway config args
  + <guide>/router/<guide>.values.yaml     # guide-specific overrides
```
