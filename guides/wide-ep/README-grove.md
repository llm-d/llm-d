# Well-Lit Path: Wide Expert Parallelism (EP/DP) with Grove

## Overview

This guide demonstrates how to deploy a DeepSeek R1 FP4 wide-EP workload on NVIDIA GB200 hardware using [Grove](https://github.com/ai-dynamo/grove). The llm-d Router, GAIE, and EPP still handle request routing, prefill/decode scheduling, and endpoint selection; Grove is responsible for creating and coordinating the multi-node model-server pods.

This variant is validated on NVIDIA GB200 hardware with Multi-Node NVLink (MNNVL), on GKE.

## Default Configuration

| Parameter | Value |
| --------- | ----- |
| Model | [nvidia/DeepSeek-R1-NVFP4](https://huggingface.co/nvidia/DeepSeek-R1-NVFP4) |
| Prefill GPUs | 4 |
| Decode Leader GPUs | 4 |
| Decode Worker GPUs | 7 workers, 4 GPUs each |
| Total GPUs | 36 |

The configuration delegates topology placement to Grove, which packs all model-server pods into the same GB200 rack.

The configuration also delegates MNNVL setup to Grove Auto-MNNVL, which creates the required `ComputeDomain` resource and claim so all pods can communicate over NVIDIA Multi-Node NVLink.

## How Grove Orchestrates This Deployment

This deployment utilizes Grove's PodCliqueSet to coordinate 9 nodes as a single logical inference system. The following describes how Grove's primitives map to the requirements of a Wide-EP workload:

- **Hierarchical gang scheduling**: Grove ensures the minimum viable combination of prefill and decode units are scheduled together, preventing resource deadlocks and ensuring service readiness
- **Topology-aware placement**: Grove leverages cluster topology to place pods optimally within NVLink domains, minimizing inter-node latency for KV-cache transfers
- **Multilevel, graceful scaling**: PodClique and PodCliqueScalingGroup primitives enable independent scaling of prefill and decode workers while maintaining correct component ratios and system integrity
- **System-level lifecycle & recovery**: Grove treats multi-component systems as single operational units. Recovery and updates operate on complete service instances, ensuring workers properly reconnect to leaders after a restart and rolling updates preserve network topology
- **Role-aware orchestration**: Defines explicit startup ordering (e.g., ensuring decode leaders are ready before workers) and role-specific configurations within a single declarative PodCliqueSet
- **Native scheduler integration**: Grove automatically translates workload intent into PodGang resources for the [KAI Scheduler](https://github.com/NVIDIA/KAI-Scheduler). This enables the scheduler to execute topology-aware placement, hierarchical queuing, optimizing the entire scheduling cycle by ensuring resources are allocated according to the specific performance and availability requirements of the workload
- **Automatic MNNVL configuration**: Grove abstracts away ComputeDomain and ResourceClaimTemplate complexity - just deploy your PodCliqueSet and Grove handles the rest
- **Coherent updates**: Grove updates compatible prefill and decode components together, preserving version compatibility and balanced serving capacity during upgrades

Grove is designed from the ground up for the unique requirements of AI inference - particularly on next-generation hardware like GB200 where maximizing NVLink fabric utilization is essential for performance.

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

* Install Grove with Auto-MNNVL and TAS support enabled, install the NVIDIA DRA driver for GPUs, create the `cluster-topology` Grove `ClusterTopologyBinding`, and install a scheduler that can schedule Grove PodGang resources. See [Multi-Node Serving Orchestration](../../docs/infrastructure/multi-node.md).
* The `cluster-topology` binding must include a `rack` domain mapped to the node label that identifies the GB200 rack/NVL72 locality boundary.
* Verify the NVIDIA DRA `ComputeDomain` CRD is available:

  ```bash
  kubectl get crd computedomains.resource.nvidia.com
  ```

* The checked-in manifest is validated on GKE GB200 and includes GKE RDMA network annotations and RDMA resource limits. For other providers, adjust the network annotations, accelerator network resources, and taints/tolerations to match the target cluster.
* Create the target namespace:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

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
kubectl get pcs,pclq,pcsg,podgang,pod -n ${NAMESPACE}
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
