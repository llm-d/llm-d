# Well-Lit Path: Wide Expert Parallelism (EP/DP) with Grove

## Overview

This guide demonstrates how to deploy a DeepSeek R1 FP4 wide-EP workload on NVIDIA GB200 hardware using Grove. The llm-d Router, GAIE, and EPP still handle request routing, prefill/decode scheduling, and endpoint selection; Grove is responsible for creating and coordinating the multi-node model-server pods.

This variant is validated on NVIDIA GB200 hardware with Multi-Node NVLink (MNNVL). The validated target topology is:

* 1 prefill pod using 4 GPUs
* 1 decode leader pod using 4 GPUs
* 7 decode worker pods using 4 GPUs each

The vLLM server loads `/models/deepseek-r1-fp4` and serves it as `deepseek-ai/DeepSeek-R1`.

## How Grove Orchestrates This Deployment

Grove models the deployment as a single `PodCliqueSet` with distinct prefill, decode-leader, and decode-worker roles. The decode roles are grouped in a `PodCliqueScalingGroup`, which lets Grove keep related decode components coordinated while preserving role-specific startup and runtime configuration.

Grove is useful for this GB200 path because it provides:

* Gang scheduling through PodGang resources and KAI Scheduler integration.
* Topology-aware placement for multi-node AI workloads.
* Coordinated lifecycle and recovery for multi-component inference deployments.
* MNNVL-oriented orchestration for GB200 systems.
* Active work on coherent updates to roll compatible prefill and decode components together while preserving balanced serving capacity.

The checked-in manifest intentionally keeps the current validation YAML intact. It still contains TODOs for follow-up cleanup: moving from explicit `ComputeDomain` wiring to Grove Auto-MNNVL, enabling TAS, setting decode worker replicas to 7, removing local PVC/model-cache assumptions, documenting provider-specific GKE/AWS differences, and replacing the temporary vLLM image when the llm-d GB200 image is ready.

## Prerequisites

* Have the [proper client tools installed](../../helpers/client-setup/README.md).
* Checkout the llm-d repo and source the shared guide environment:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="wide-ep"
  export NAMESPACE=llm-d-wide-ep
  export MODEL=deepseek-ai/DeepSeek-R1
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

* Install Grove with Auto-MNNVL and TAS support enabled, and install a scheduler that can schedule Grove PodGang resources. See [Multi-Node Serving Orchestration](../../docs/infrastructure/multi-node.md).
* Create the target namespace:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

* Ensure the model weights are available at `/models/deepseek-r1-fp4` in the model-server pods. The current validation manifest expects a `shared-model-cache` PVC and keeps this assumption as a tracked TODO.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

1. Deploy a Kubernetes Gateway by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. Deploy the llm-d Router and an HTTPRoute that connects it to the Gateway:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART}  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Grove Model Server

Apply the Grove manifest directly:

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/grove/serviceAccount.yaml
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/grove/podCliqueSet.yaml
```

### 3. Verify the Deployment

Check that Grove created the expected hierarchy:

```bash
kubectl get pcs,pclq,pcsg,pg,pod -n ${NAMESPACE}
```

Resolve the router endpoint:

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

Send a request from inside the cluster:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="${NAMESPACE}" \
    --env="IP=${IP}" \
    -- /bin/bash
```

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "deepseek-ai/DeepSeek-R1",
        "prompt": "How are you today?"
    }' | jq
```

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/grove/podCliqueSet.yaml
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/grove/serviceAccount.yaml
```
