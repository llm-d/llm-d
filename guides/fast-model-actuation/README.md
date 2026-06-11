# Fast Model Actuation

## Overview

Fast Model Actuation (FMA) enables rapid model loading and switching for LLM inference on Kubernetes by exploiting vLLM sleep/wake and model swapping. It implements the "dual pods" technique where server-requesting Pods describe desired inference servers while server-providing Pods actually run vLLM. A Kubernetes controller manages the lifecycle, binding, sleep/wake, and readiness relay between these pod pairs.

This approach dramatically reduces model actuation latency compared to cold-starting new pods, enabling efficient GPU utilization across multiple models.

## Default Configuration

| Parameter                | Value                                                                      |
| ------------------------ | -------------------------------------------------------------------------- |
| Model                    | [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)               |
| Replicas (requesting)   | 2                                                                          |
| Replicas (providing)    | 2                                                                          |
| GPUs per providing pod  | 1                                                                          |

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Set the following environment variables:

  ```bash
  export NAMESPACE=llm-d-fma
  export FMA_CHART_INSTANCE_NAME="fma"
  export FMA_VERSION="0.6.0-alpha.13"
  ```

- Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

## Installation Instructions

### 1. Apply FMA CRDs

```bash
FMA_CRD_BASE="https://raw.githubusercontent.com/llm-d-incubation/llm-d-fast-model-actuation/v${FMA_VERSION}/config/crd"
kubectl apply --server-side \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_inferenceserverconfigs.yaml \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_launcherconfigs.yaml \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_launcherpopulationpolicies.yaml
kubectl wait --for=condition=Established crd/inferenceserverconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherpopulationpolicies.fma.llm-d.ai --timeout=120s
```

### 2. Deploy FMA Controllers via Helm

```bash
helm upgrade --install ${FMA_CHART_INSTANCE_NAME} \
    oci://ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/charts/fma-controllers \
    --version ${FMA_VERSION} \
    -n ${NAMESPACE}
```

### 3. Wait for Controllers to Be Ready

```bash
kubectl wait --for=condition=available --timeout=180s \
    deployment "${FMA_CHART_INSTANCE_NAME}-dual-pods-controller" -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s \
    deployment "${FMA_CHART_INSTANCE_NAME}-launcher-populator" -n ${NAMESPACE}
```

## Verification

Verify the FMA controllers are running:

```bash
kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/instance=${FMA_CHART_INSTANCE_NAME}
```

Both the `dual-pods-controller` and `launcher-populator` deployments should show `Available=True`.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
kubectl delete crd inferenceserverconfigs.fma.llm-d.ai launcherconfigs.fma.llm-d.ai launcherpopulationpolicies.fma.llm-d.ai
```
