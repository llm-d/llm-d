# Gateway Recipes

This directory contains recipes for deploying a `Gateway` called `llm-d-inference-gateway`.

> [!NOTE]
> You will need to deploy an `HTTPRoute` to connect your `Gateway` to an `InferencePool`.

> [!WARNING]
> The `kgateway` and `kgateway-openshift` recipes are deprecated in llm-d and will be removed in the next release. Prefer `agentgateway` for new self-installed inference deployments. These recipes are retained only to support migrations during the current release.

A `Gateway` can be deployed with:

```bash
kubectl apply -k ${YOUR_GATEWAY} -n ${NAMESPACE}
```
