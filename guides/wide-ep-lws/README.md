# Wide Expert Parallelism

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-intel-acc-xpu-vllm-x.yaml)

## Overview

This guide demonstrates how to deploy DeepSeek-R1-0528 using vLLM's P/D disaggregation support with NIXL in a wide expert parallel pattern with DP-aware scheduling. This guide includes both `LeaderWorkerSet` and `DisaggregatedSet` deployment paths. It has been validated on:

* a 32xH200 cluster with InfiniBand networking
* a 32xH200 cluster on GKE with RoCE networking
* a 32xB200 cluster on GKE with RoCE networking

> [!NOTE]
> This guide uses a custom vLLM image built by llm-d to solve two issues:
> A) NVSHMEM bug on RoCE impacting DeepEP HT - llm-d vendors a custom patch
> B) vLLM v0.23.0-v0.24.0 bug with DP supervisor - llm-d builds a custom image
>
> We plan to migrate to the upstream vLLM images in an upcoming release

## Default Configuration

| Parameter                | Value                                                   |
| ------------------------ | ------------------------------------------------------- |
| Model                    | [DeepSeek-R1-0528](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528) |
| Prefill Data Parallelism | 16                                                      |
| Decode Data Parallelism  | 16                                                      |
| Total GPUs               | 32                                                      |

### Intel XPU Configuration

The Intel XPU configuration uses the validated DeepSeek-V2-Lite shape:

| Parameter | Value |
| --- | --- |
| Model | [DeepSeek-V2-Lite-Chat](https://huggingface.co/deepseek-ai/DeepSeek-V2-Lite-Chat) |
| Prefill Tensor Parallelism | 2 |
| Decode Tensor Parallelism | 2 |
| Total XPUs | 4 |
| Expert Parallelism | enabled |
| All2All backend | `allgather_reducescatter` |
| KV transfer | NIXL with `kv_buffer_device=xpu` |
| UCX transport | `tcp,ze_copy` for the validated non-RDMA configuration |

### Tested Hardware Backends

This guide includes configurations for the following accelerators:

| Backend               | Directory                                   | Notes                                      |
| --------------------- | ------------------------------------------- | ------------------------------------------ |
| NVIDIA GPU (GKE)      | `modelserver/gpu/vllm/gke/`                 | GKE deployment (H200)                      |
| NVIDIA GPU (GKE A4)   | `modelserver/gpu/vllm/topology-aware/gke-a4/` | GKE deployment (B200)                    |
| NVIDIA GPU (CoreWeave)| `modelserver/gpu/vllm/coreweave/`           | CoreWeave deployment                       |
| NVIDIA GPU (GB200)    | `modelserver/gpu/vllm/dgx-cloud-gb200/`     | DGX Cloud GB200 deployment                 |
| Intel XPU (vLLM)      | `modelserver/xpu/vllm/`                     | DeepSeek-V2-Lite-Chat, DRA `gpu.intel.com`, XCCL, NIXL XPU KV buffers |

> [!NOTE]
> NVIDIA GPU backends that use DeepEP for inter-node EP require All-to-All RDMA
> connectivity. Every NIC on a host must be able to communicate with every NIC
> on all other hosts. Networks restricted to communicating only between matching
> NIC IDs (rail-only connectivity) will fail. The Intel XPU backend uses XCCL
> and `allgather_reducescatter`; it does not use DeepEP, but still requires
> full-mesh pod network connectivity between decode and prefill workers.

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```
* Set the following environment variables:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="wide-ep-lws"
  export NAMESPACE=llm-d-wide-ep
  export MODEL=deepseek-ai/DeepSeek-R1-0528
  ```
* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```
* You have deployed the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/).
* To use the `DisaggregatedSet` path, install LWS `v0.9.0` or newer. When installing with Helm, pass `--set enableDisaggregatedSet=true` to enable the `DisaggregatedSet` validating webhook and RBAC.
* For Intel XPU, install the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) and verify that the `gpu.intel.com` DRA DeviceClass is available.
* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```
* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

For Intel XPU, add the XPU router override so EPP targets the single decode
sidecar port exposed by the XPU manifests:

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/xpu.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. *Deploy the llm-d Router and an HTTPRoute* that connects it to the Gateway as follows:

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

For Intel XPU, include
`-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/xpu.values.yaml` after the
`${GUIDE_NAME}.values.yaml` file.

</details>

### 2. Deploy the Model Server

Choose one of the following deployment paths:

#### Deploy using LeaderWorkerSet

Apply the Kustomize overlay for your specific backend:

```bash
# NVIDIA GPU
export INFRA_PROVIDER=gke # options: gke, coreweave, dgx-cloud-gb200
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}

# Intel XPU
export MODEL=deepseek-ai/DeepSeek-V2-Lite-Chat
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm
```

#### Deploy using DisaggregatedSet

Apply the `DisaggregatedSet` overlay:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/disaggregatedset
```

### 3. (Optional) Enable Monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).
* Deploy the monitoring resources for model servers:

> With DP-aware scheduling, each DP rank is available at podip:port, where each
> port is `rank0`-`rank7`. This guide ships an overlay for the monitoring
> that scapes each rank's port.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
```

### 4. (Optional) Topology Aware Scheduling (TAS)

For information on how to use topology aware scheduling using Kueue, see [LWS + TAS user guide](https://lws.sigs.k8s.io/docs/examples/tas/). To deploy the guide with TAS enabled, use the following command:

```bash
# H200 on GKE
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke
# B200 on GKE
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke-a4
```

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    --env="MODEL=$MODEL" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d "{
        \"model\": \"${MODEL}\",
        \"prompt\": \"How are you today?\"
    }" | jq
```

## Benchmarking

This guide uses [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf).

`inference-perf.yaml` runs concurrent load with `concurrency_level=2048` and `num_requests=8192` and is shaped to highlight the strengths of wide expert parallelism for throughput oriented workloads. The following creates a job to run against the standalone mode stack:

```bash
kubectl apply -f inference-perf.yaml
```

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
# If you enabled monitoring (Step 3), remove the monitoring overlay first.
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
# NVIDIA GPU
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
# Intel XPU
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm
```

## Benchmarking Results

### CKS (4x H200, 32 GPUs, InfiniBand)

Benchmark: `2048_concurrent_2k_isl_2k_osl` (2048 concurrent requests, 2K input / 2K output tokens)

| Metric | DP Supervisor |
|---|---|
| Output tokens/s | 25,176 |
| Input tokens/s | 25,122 |
| Total tokens/s | 50,299 |
| Requests/s | 12.6 |

~1,600 output tokens/s per decode GPU (16 decode GPUs).
