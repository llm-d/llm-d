# optimized baseline - Scheduler - REMOVE THIS README LATER

This guide adds a single override: the `llm-d.ai/guide: optimized-baseline` match label so the scheduler discovers the correct model server pods.

Layer this file on top of the shared [scheduler recipes](../../recipes/scheduler/).

## EPHEMERAL DOC

We no longer want to have per guide docs explaining anything related to installation and verification. All of that should be abstracted into a few docs at the root of guide repo. This Document is kept around for reviews to understand the scoping and implementation of this refactor, as we will be refactoring guide by guide to cut down review overhead.

## Standalone (no gateway)

```bash
helm install optimized-baseline-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \ 
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/optimized-baseline/scheduler/optimized-baseline.values.yaml \
  -n ${NAMESPACE} --version v1.4.0
```

## Gateway

```bash
GATEWAY_PROVIDER=none # options include: ["none", "gke", "istio"]
helm install optimized-baseline-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/optimized-baseline/scheduler/optimized-baseline.values.yaml \
  --set "provider.name=${GATEWAY_PROVIDER}" \
  -n ${NAMESPACE} --version v1.4.0
```
