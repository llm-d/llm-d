# Gateway Recipes

This directory contains recipes for deploying the `llm-d-inference-gateway` and `llm-d-route`.

## Prerequisites

Before using these recipes, you must have a Kubernetes cluster with the corresponding gateway control plane installed. Refer to the [gateway provider doc](../../prereq/gateway-provider/README.md) for more information.

> [!WARNING]
> The `kgateway` and `kgateway-openshift` recipes are deprecated in llm-d and will be removed in the next release. Prefer `agentgateway` for new self-installed inference deployments. These recipes are retained only to support migrations during the current release.

## Installation

The following recipes are available for deploying the gateway and httproute.

<!-- TABS:START -->

<!-- TAB:GKE L7 Regional External Managed:default -->
### GKE L7 Regional External Managed

This deploys a gateway suitable for GKE, using the `gke-l7-regional-external-managed` gateway class.

```bash
kubectl apply -k ./gke-l7-regional-external-managed -n ${NAMESPACE}
```

<!-- TAB:GKE L7 Regional Internal Managed -->
### GKE L7 Regional Internal Managed

This deploys a gateway suitable for GKE, using the `gke-l7-rilb` gateway class.

```bash
kubectl apply -k ./gke-l7-rilb -n ${NAMESPACE}
```

<!-- TAB:Istio -->
### Istio

This deploys a gateway suitable for Istio, using the `istio` gateway class.

```bash
kubectl apply -k ./istio -n ${NAMESPACE}
```

<!-- TAB:Agentgateway -->
### Agentgateway

This deploys a gateway suitable for `agentgateway`, using the `agentgateway` gateway class. This is the preferred self-installed inference gateway recipe in llm-d.

```bash
kubectl apply -k ./agentgateway -n ${NAMESPACE}
```

<!-- TAB:Agentgateway (OpenShift) -->
### Agentgateway (OpenShift)

This deploys the preferred OpenShift-oriented `agentgateway`
recipe. The rendered `Gateway` uses the `agentgateway` GatewayClass and an
OpenShift-oriented `AgentgatewayParameters` resource.

```bash
kubectl apply -k ./agentgateway-openshift -n ${NAMESPACE}
```

<!-- TAB:KGateway -->
### KGateway

This deploys the legacy `kgateway` recipe. It is deprecated in llm-d, will be
removed in a future release, and is retained only to support migration
to `agentgateway`. The recipe directory name is retained for
compatibility, but the rendered `Gateway` uses the `agentgateway` GatewayClass.

```bash
kubectl apply -k ./kgateway -n ${NAMESPACE}
```

<!-- TAB:KGateway (OpenShift) -->
### KGateway (OpenShift)

This deploys the legacy OpenShift-oriented `kgateway` recipe. It is deprecated
in llm-d, will be removed in a future release, and is retained only to
support migration to `agentgateway`. The recipe directory name is
retained for compatibility, but the rendered `Gateway` uses the
`agentgateway` GatewayClass and an OpenShift-oriented
`AgentgatewayParameters` resource.

```bash
kubectl apply -k ./kgateway-openshift -n ${NAMESPACE}
```

<!-- TABS:END -->

## Verification

You can verify the installation by checking the status of the created resources.

### Check the Gateway

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`. The `CLASS` will vary depending on the recipe you deployed.

```text
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         1m
```

### Check the HTTPRoute

```bash
kubectl get httproute -n ${NAMESPACE}
```

```text
NAME          HOSTNAMES   AGE
llm-d-route               1m
```
