# Autoscaling Across Multiple InferencePools with WVA

This guide demonstrates how a single WVA controller makes optimization decisions
across multiple InferencePools in the same watch scope using the
`GreedyByScoreOptimizer`.

## Overview

WVA supports two optimizer modes:

- **`CostAwareOptimizer`** (default): Each InferencePool is evaluated
  independently. WVA scales up the cheapest variant and scales down the most
  expensive within each pool, with no awareness of other pools.

- **`GreedyByScoreOptimizer`** (`enableLimiter: true`): WVA considers all
  eligible InferencePools in its watch scope together. It fair-shares available
  GPU resources across pools based on priority scores rather than optimizing
  each pool in isolation.

The `GreedyByScoreOptimizer` is useful when multiple models share a fixed GPU
budget. With the default `CostAwareOptimizer`, a pool that has scaled up to
handle a spike will hold its resources even after another pool starts building
a queue — because each pool has no visibility into the other. The
`GreedyByScoreOptimizer` addresses this by making allocation decisions across
all pools simultaneously.

> [!NOTE]
> WVA operates per-cluster or per-namespace, not per InferencePool. A single
> WVA controller monitors all InferencePools in its watch scope and makes
> optimization decisions across them together.

> [!NOTE]
> Cross-pool re-scaling — where one pool actively scales down to free capacity
> for another — is not yet supported. This guide demonstrates the current
> multi-pool optimization behavior using the `GreedyByScoreOptimizer`.

For a more complete multi-model setup including gateway, HTTPRoute, and EPP
configuration, see the
[multi-model-wva benchmark config](https://github.com/llm-d/llm-d-benchmark/blob/main/config/scenarios/examples/multi-model-wva.yaml).

## Prerequisites

- WVA installed and running. Follow the [WVA installation guide](../README.wva.md).
- Two inference deployments running in the same namespace, each with its own
  InferencePool. This guide assumes the deployments and InferencePools already
  exist. See the [optimized-baseline autoscaling guide](../optimized-baseline-autoscaling)
  for reference.
- Prometheus and Prometheus Adapter configured as described in the
  [WVA installation guide](../README.wva.md#install-prometheus-adapter-required-dependency).

## Set Namespaces

```bash
export NAMESPACE=llm-d-optimized-baseline
export WVA_NAMESPACE=llm-d-autoscaler
```

## Step 1: Enable GreedyByScoreOptimizer

Apply the ConfigMap that switches WVA to cross-pool aware mode:

```bash
kubectl apply -f wva-greedy-config.yaml -n ${WVA_NAMESPACE}
```

This enables `enableLimiter: true`, activating the `GreedyByScoreOptimizer` so
WVA considers all eligible InferencePools in its watch scope together rather
than evaluating each pool independently.

## Step 2: Apply HPA Resources

```bash
kubectl apply -k guides/workload-autoscaling/multi-inferencepool -n ${NAMESPACE}
```

This creates an HPA for each model, annotated with `llm-d.ai/managed: "true"`
so WVA discovers and manages them automatically.

> **Note:** The manifests in this guide use `llama-8b-model-a` and
> `qwen-32b-model-b` as example deployment names. Replace these with the actual
> names of your existing Deployments.

## Step 3: Verify

Check that both HPAs are created and reading the WVA metric:

```bash
kubectl get hpa -n ${NAMESPACE}
```

Expected output (example):

```text
NAME               REFERENCE                    TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
llama-8b-model-a   Deployment/llama-8b-model-a  0/1       1         8         1          5m
qwen-32b-model-b   Deployment/qwen-32b-model-b  0/1       1         8         1          5m
```

Confirm WVA is managing the HPAs by checking annotations:

```bash
kubectl get hpa llama-8b-model-a -n ${NAMESPACE} -o jsonpath='{.metadata.annotations}' | jq .
```

Expected output includes `"llm-d.ai/managed": "true"`.

## Step 4: Observe WVA Decisions Across Multiple Pools

With `GreedyByScoreOptimizer` enabled, WVA emits `wva_desired_replicas` for
each pool based on a globally-scoped decision rather than independent per-pool
calculations. You can observe this behavior by watching the HPAs under load:

```bash
kubectl get hpa -n ${NAMESPACE} -w
```

To inspect the raw optimization decisions:

```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/wva_desired_replicas" | jq .
```

When both pools are active, you should see `wva_desired_replicas` values that
reflect the relative demand across both pools. Under the default
`CostAwareOptimizer`, each pool would scale independently; with
`GreedyByScoreOptimizer`, the desired replica counts are computed with awareness
of the total GPU budget shared across pools.

### Demonstrating the Balancing Behavior

Send inference requests to both models and observe how WVA computes desired
replica counts across the two pools:

```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/wva_desired_replicas" | jq .
```

With `CostAwareOptimizer` (default), each pool's `wva_desired_replicas` is
computed independently based on its own saturation signals. With
`GreedyByScoreOptimizer`, the values reflect a joint decision across all pools
in the watch scope. You can compare behavior by toggling `enableLimiter` in the
`wva-saturation-scaling-config` ConfigMap and observing how the desired replica
counts differ under the same load.

For a complete load generation setup across multiple models, see the
[multi-model-wva benchmark config](https://github.com/llm-d/llm-d-benchmark/blob/main/config/scenarios/examples/multi-model-wva.yaml).

## Cleanup

```bash
kubectl delete -k guides/workload-autoscaling/multi-inferencepool -n ${NAMESPACE}
kubectl delete configmap wva-saturation-scaling-config -n ${WVA_NAMESPACE}
```
