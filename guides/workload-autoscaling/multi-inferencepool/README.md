# Autoscaling Across Multiple InferencePools with WVA


This guide demonstrates how a single WVA controller observes and makes
optimization decisions across multiple InferencePools in the same watch scope.

## Overview

By default, WVA uses the `CostAwareOptimizer` to make capacity decisions based
on demand and variant cost. When `enableLimiter: true` is enabled, WVA switches
to the `GreedyByScoreOptimizer`, which considers all eligible InferencePools in
its watch scope.

> [!NOTE]
> WVA operates per-cluster or per-namespace, not per InferencePool. A single
> WVA controller monitors all InferencePools in its watch scope and makes
> scaling decisions across all eligible InferencePools together.

> [!NOTE]
> Cross-pool re-scaling — where one pool actively scales down to free capacity
> for another — is not yet supported. This guide demonstrates the current
> multi-pool optimization behavior using the `GreedyByScoreOptimizer`.

## Prerequisites

- WVA installed and running. Follow the [WVA installation guide](../README.wva.md).
- Two inference deployments running in the same namespace, each with its own
  InferencePool. This guide assumes the deployments and InferencePools already
  exist. See the [optimized-baseline autoscaling guide](../optimized-baseline-autoscaling) for reference.
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
This enables `enableLimiter: true`, activating the `GreedyByScoreOptimizer` so WVA considers all eligible InferencePools in its watch scope together.

## Step 2: Apply VariantAutoscaling and HPA Resources

```bash
kubectl apply -k guides/workload-autoscaling/multi-inferencepool -n ${NAMESPACE}
```

This creates:
- A `VariantAutoscaling` CR for each model, linking WVA to each Deployment
- An `HPA` for each model, reading `wva_desired_replicas` from Prometheus


> **Note:** The manifests in this guide use `llama-8b-model-a` and `qwen-32b-model-b` as example deployment names.
> Replace these with the actual names of your existing Deployments and update `modelID` to match.
## Step 3: Verify

Check that both `VariantAutoscaling` resources are created and metrics are available:

```bash
kubectl get variantautoscaling -n ${NAMESPACE}
```

Expected output (example):
```text
NAME               TARGET             MODEL                OPTIMIZED   METRICSREADY   AGE
llama-8b-model-a   llama-8b-model-a   meta/llama-3.1-8b   1           True           5m
qwen-32b-model-b   qwen-32b-model-b   Qwen/Qwen3-32B      1           True           5m
```

Check both HPAs are reading the WVA metric:

```bash
kubectl get hpa -n ${NAMESPACE}
```

## Step 4: Observe WVA Decisions Across Multiple Pools

Watch how WVA computes desired replica counts for both pools:

```bash
kubectl get hpa -n ${NAMESPACE} -w
```

You can inspect the per-pool optimization decisions emitted by WVA via Prometheus:

```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/wva_desired_replicas" | jq .
```

The `wva_desired_replicas` metric is emitted per variant. With
`GreedyByScoreOptimizer` enabled, these values reflect decisions that consider
all eligible InferencePools in its watch scope rather than independent per-pool calculations.

## Cleanup

```bash
kubectl delete -k guides/workload-autoscaling/multi-inferencepool -n ${NAMESPACE}
kubectl delete configmap wva-saturation-scaling-config -n ${WVA_NAMESPACE}
```
