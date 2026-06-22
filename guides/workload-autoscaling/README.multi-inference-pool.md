# Multi-Inference Pool Setup

This guide adds **additional InferencePools** to an existing [optimized-baseline](../optimized-baseline/README.md) deployment. Each additional pool gets its own EPP and model server Deployment in the same namespace. Repeat the steps below for every pool you want to add.

## Prerequisites

Complete the [optimized-baseline](../optimized-baseline/README.md) guide. At the end of that guide you should have one Helm release (`optimized-baseline`), one InferencePool, one EPP, and model server pods running in the `llm-d-optimized-baseline` namespace.

> [!NOTE]
> InferencePools can also be deployed in **separate namespaces**.

## Step 1: Deploy an Additional Helm Release

Install an additional Helm release in the same namespace as the optimized-baseline. Each release must use a **unique `matchLabels`** selector so its InferencePool discovers only the correct model's pods. The example below adds a pool called `model-b`; repeat with a different release name and values file for every additional pool.

```bash
export NAMESPACE=llm-d-optimized-baseline
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export ROUTER_CHART_VERSION=v0

helm install model-b \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/workload-autoscaling/multi-inference-pool/model-b.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

> [!WARNING]
> The standalone chart creates a `ConfigMap` named `envoy` with a hardcoded name (not prefixed with the release name). Installing another release in the same namespace will fail with an ownership conflict on this ConfigMap. To work around this, reassign the ConfigMap's Helm ownership annotations to the new release before installing it:
> ```bash
> kubectl annotate configmap envoy -n ${NAMESPACE} \
>   meta.helm.sh/release-name=model-b meta.helm.sh/release-namespace=${NAMESPACE} --overwrite
> kubectl label configmap envoy -n ${NAMESPACE} \
>   app.kubernetes.io/managed-by=Helm --overwrite
> ```

The values file sets a unique pool selector via `router.modelServers.matchLabels`. See [`model-b.values.yaml`](./multi-inference-pool/model-b.values.yaml) for an example. Create a similar values file for each additional pool, ensuring every pool uses a distinct `matchLabels` selector so InferencePools do not cross-select each other's pods.

> [!NOTE]
> Replace `model-b` with your actual model identifier in the values file.

## Step 2: Deploy the Model Server

Deploy the model server for the new pool the same way as the [optimized-baseline](../optimized-baseline/README.md#2-deploy-the-model-server), with its Kustomize overlay setting the matching `llm-d.ai/model` label. Ensure the Deployment's pod template labels match the `matchLabels` in the corresponding Helm values file. If they don't match, the InferencePool will not discover the pods and the EPP will have no endpoints to route to.

## Verification

```bash
# Confirm all InferencePools and EPP services
kubectl get inferencepools,svc -n ${NAMESPACE}

# Confirm model server pods are discovered by their pools
kubectl get pods -n ${NAMESPACE} --show-labels
```

## Configuring Autoscaling

Once the additional pools are deployed, configure autoscaling by creating an HPA per model. Either scaling path can be used:

- **[HPA + EPP Metrics](./README.hpa-epp.md)**: Create one HPA per model using EPP metrics (`epp_queue_size`, `epp_running_requests`). Each HPA's Prometheus Adapter rules should filter by the corresponding InferencePool name.

- **[HPA + WVA Metrics](./README.wva.md)**: Create one HPA per model using the `wva_desired_replicas` metric. Each HPA must carry the WVA discovery annotations (`llm-d.ai/managed`, `llm-d.ai/model-id`, `llm-d.ai/variant-cost`).

## Cleanup

Uninstall each additional release you added:

```bash
helm uninstall model-b -n ${NAMESPACE}
```
