# Gateway Recipes

This directory contains recipes for deploying a `Gateway` called `llm-d-inference-gateway`.

> [!NOTE]
> You will need to deploy an `HTTPRoute` to connect your `Gateway` to an `InferencePool`. The `InferencePool` helm chart has the option to automatically do that.

> [!WARNING]
> The `kgateway` and `kgateway-openshift` recipes are deprecated in llm-d and will be removed in the next release. Prefer `agentgateway` for new self-installed inference deployments. These recipes are retained only to support migrations during the current release.

Available recipes: `agentgateway`, `envoy-ai-gateway`, `istio`, `gke-l7-rilb`, `gke-l7-regional-external-managed`.

A `Gateway` can be deployed with:

```bash
kubectl apply -k ${YOUR_GATEWAY} -n ${NAMESPACE}
```
