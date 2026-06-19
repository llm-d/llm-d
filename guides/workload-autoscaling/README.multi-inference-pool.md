# Multi-Inference Pool Setup

This guide shows how to deploy **multiple InferencePools in a single namespace**, each with its own EPP and model server Deployment.

## Overview

The [optimized-baseline](../optimized-baseline/README.md) guide deploys one model with a single Helm release (InferencePool + EPP) and one model server Deployment. To serve multiple models in the same namespace, you repeat that pattern with one Helm release and one model server Deployment per model, ensuring each release uses **distinct `matchLabels`** so InferencePools don't cross-select each other's pods.

## Prerequisites

1. The [optimized-baseline](../optimized-baseline/README.md) prerequisites completed (client tools, repo checkout, GAIE CRDs).
2. A monitoring stack configured per the [autoscaling prerequisites](README.md#prerequisites).

## Step 1: Deploy Multiple Helm Releases

Install one Helm release per model in the same namespace. Each release must use a **unique `matchLabels`** selector so its InferencePool discovers only the correct model's pods.

```bash
export NAMESPACE=llm-d-multi-pool
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export ROUTER_CHART_VERSION=v0

kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

# Model A
helm install model-a \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/workload-autoscaling/multi-inference-pool/model-a.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}

# Model B
helm install model-b \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/workload-autoscaling/multi-inference-pool/model-b.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

> [!WARNING]
> The standalone chart creates a `ConfigMap` named `envoy` with a hardcoded name (not prefixed with the release name). Installing a second release in the same namespace will fail with an ownership conflict on this ConfigMap. To work around this, reassign the ConfigMap's Helm ownership annotations to the second release before installing it:
> ```bash
> kubectl annotate configmap envoy -n ${NAMESPACE} \
>   meta.helm.sh/release-name=model-b meta.helm.sh/release-namespace=${NAMESPACE} --overwrite
> kubectl label configmap envoy -n ${NAMESPACE} \
>   app.kubernetes.io/managed-by=Helm --overwrite
> ```

Each values file sets a unique pool selector via `router.modelServers.matchLabels`. See [`model-a.values.yaml`](./multi-inference-pool/model-a.values.yaml) and [`model-b.values.yaml`](./multi-inference-pool/model-b.values.yaml) for the defaults.

> [!NOTE]
> Replace `model-a` / `model-b` with your actual model identifiers in the values files.

## Step 2: Deploy Model Servers

Deploy the model servers the same way as the [optimized-baseline](../optimized-baseline/README.md#2-deploy-the-model-server), with each model's Kustomize overlay setting the matching `llm-d.ai/model` label. Ensure each Deployment's pod template labels match the `matchLabels` in the corresponding Helm values file. If they don't match, the InferencePool will not discover the pods and the EPP will have no endpoints to route to.

## Verification

```bash
# Confirm two InferencePools and EPP services
kubectl get inferencepools,svc -n ${NAMESPACE}

# Confirm model server pods are discovered by their pools
kubectl get pods -n ${NAMESPACE} --show-labels
```

## Configuring Autoscaling

Once the multi-pool infrastructure is deployed, configure autoscaling by creating an HPA per model. Either scaling path can be used:

- **[HPA + EPP Metrics](./README.hpa-epp.md)**: Create one HPA per model using EPP metrics (`epp_queue_size`, `epp_running_requests`). Each HPA's Prometheus Adapter rules should filter by the corresponding InferencePool name.

- **[HPA + WVA Metrics](./README.wva.md)**: Create one HPA per model using the `wva_desired_replicas` metric. Each HPA must carry the WVA discovery annotations (`llm-d.ai/managed`, `llm-d.ai/model-id`, `llm-d.ai/variant-cost`).

## Cleanup

```bash
helm uninstall model-a model-b -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```
