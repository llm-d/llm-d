# llm-d Stack Helm Chart

This chart is a unified entrypoint for deploying an llm-d router plus
deployment-based model server profiles.

It wraps the existing router charts as dependencies:

- `routerStandalone`: `llm-d-router-standalone`
- `routerGateway`: `llm-d-router-gateway`

The model server workloads are rendered by this chart from `modelServers.*`.
Cluster-level prerequisites remain outside this chart: Gateway API Inference
Extension CRDs, accelerator drivers or DRA drivers, Gateway provider
controllers, Prometheus Operator CRDs, and provider-specific RDMA setup.

## Build Dependencies

```bash
helm dependency build charts/llm-d-stack
```

## Optimized Baseline

```bash
helm install optimized-baseline charts/llm-d-stack \
  -n llm-d-optimized-baseline \
  --create-namespace
```

## P/D Disaggregation

```bash
helm install pd-disaggregation charts/llm-d-stack \
  -f charts/llm-d-stack/profiles/pd-disaggregation.yaml \
  -n llm-d-pd-disaggregation \
  --create-namespace
```

## Predicted Latency Routing

```bash
helm install predicted-latency charts/llm-d-stack \
  -f charts/llm-d-stack/profiles/predicted-latency-routing.yaml \
  -n llm-d-predicted-latency \
  --create-namespace
```

## Precise Prefix Cache Routing

```bash
helm install precise-prefix-cache charts/llm-d-stack \
  -f charts/llm-d-stack/profiles/precise-prefix-cache-routing.yaml \
  -n llm-d-precise-prefix-cache \
  --create-namespace
```

## Tiered Prefix Cache

```bash
helm install tiered-prefix-cache charts/llm-d-stack \
  -f charts/llm-d-stack/profiles/tiered-prefix-cache.yaml \
  -n llm-d-tiered-prefix-cache \
  --create-namespace
```

## Gateway Mode

Profiles default to standalone router mode. To use the gateway router chart:

```bash
helm install optimized-baseline charts/llm-d-stack \
  --set routerStandalone.enabled=false \
  --set routerGateway.enabled=true \
  --set routerGateway.provider.name=gke \
  --set routerGateway.httpRoute.create=true \
  --set routerGateway.httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n llm-d-optimized-baseline \
  --create-namespace
```

## Hugging Face Token

By default, model server pods reference an existing secret named
`llm-d-hf-token` with key `HF_TOKEN`.

To create it through Helm:

```bash
helm install optimized-baseline charts/llm-d-stack \
  --set hfToken.create=true \
  --set-string hfToken.value="${HF_TOKEN}" \
  -n llm-d-optimized-baseline \
  --create-namespace
```

## Current Scope

This first chart version supports deployment-based model server paths:

- optimized baseline
- P/D disaggregation
- predicted latency routing
- precise prefix cache routing
- tiered prefix cache using vLLM native CPU offload

WideEP / LeaderWorkerSet profiles are intentionally not templated yet because
they need LWS, topology-aware scheduling, and provider-specific DRA/RDMA
objects. Use `extraObjects` as an escape hatch until native LWS templates are
added.
