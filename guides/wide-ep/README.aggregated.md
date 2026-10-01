# Wide Expert Parallelism (Aggregated / Non-P/D Mode)

## Overview

This guide demonstrates how to deploy `deepseek-ai/DeepSeek-R1-0528` using vLLM's Wide Expert Parallelism (`DP=16, EP=16, TP=1`) with DP-aware scheduling in **aggregated mode** (unified prefill + decode without P/D disaggregation).

Whereas the default [disaggregated Wide-EP guide](README.md) requires **32 GPUs** (16 prefill + 16 decode across 4 nodes), this configuration deploys a single `LeaderWorkerSet` (`size: 2`) across **2 × 8-GPU nodes (16 GPUs total)**, such as **2 × GKE A4 (`a4-highgpu-8g`, 16 × NVIDIA B200)** or **2 × A3 Ultra (`a3-ultragpu-8g`, 16 × NVIDIA H200)** nodes.

### Key Differences from Disaggregated Wide-EP

1. **Half the GPU footprint (16 GPUs vs. 32 GPUs):** Runs a single 2-node (`size: 2`) `LeaderWorkerSet` where each pod runs 8 DP ranks (`DP_SIZE_LOCAL=8`, `TP_SIZE=1`, total `DP=16` / `EP=16`).
2. **No NIXL KV-transfer or routing sidecar:** Each vLLM worker processes both chunked prefill and decode locally and exposes ports `8000`–`8007` (`rank0`–`rank7`) directly to the `llm-d` Endpoint Picker (`EPP`).
3. **Single-profile EPP routing (`router/wide-ep-aggregated.values.yaml`):** Routes requests across all 16 DP ranks (`targetPorts: 8000..8007`) using `prefix-cache-scorer`, `queue-scorer`, and `active-request-scorer` without `always-disagg-pd-decider`.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [DeepSeek-R1-0528](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528) |
| Workload Topology | Aggregated (Unified Prefill + Decode) |
| Data Parallelism (`DP`) | 16 (8 ranks per node × 2 nodes) |
| Expert Parallelism (`EP`) | 16 (`--enable-expert-parallel`) |
| Tensor Parallelism (`TP`) | 1 |
| All2All Backend | `deepep_low_latency` (`--enable-dbo`) |
| Total GPUs | **16** (2 × 8-GPU B200 or H200 nodes) |

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md).
* A Kubernetes cluster with **2 × 8-GPU RDMA-capable nodes** (e.g., GKE `a4-highgpu-8g` with 16 × NVIDIA B200 GPUs and RoCE/DRANET enabled).
  * On GKE, verify that `gke-managed-networking-dra-driver` is publishing `ResourceSlices` for both GPU nodes:
    ```bash
    kubectl label nodes -l cloud.google.com/gke-accelerator=nvidia-b200 \
      cloud.google.com/gke-networking-dra-driver=true --overwrite
    kubectl get resourceslices
    ```
* Set the environment variables:
  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="wide-ep"
  export NAMESPACE="llm-d-wide-ep"
  export MODEL="deepseek-ai/DeepSeek-R1-0528"
  ```
* Install the Gateway API Inference Extension CRDs:
  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
  ```
* Install the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/):
  ```bash
  kubectl apply --server-side -f https://github.com/kubernetes-sigs/lws/releases/latest/download/manifests.yaml
  ```
* Create the target namespace and `llm-d-hf-token` secret:
  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic llm-d-hf-token \
    --from-literal=HF_TOKEN="${HF_TOKEN}" \
    -n ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

## Installation Instructions

### 1. Deploy the llm-d Router (Aggregated Profile)

Deploy the standalone `llm-d` router using [`router/wide-ep-aggregated.values.yaml`](router/wide-ep-aggregated.values.yaml), which configures DP multi-port routing (`8000`–`8007`) with a single `default` scheduling profile:

```bash
helm upgrade --install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/wide-ep-aggregated.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

> [!TIP]
> If your cluster does not have a dedicated large CPU node pool (`e2-standard-16` or larger), you can allow the `wide-ep-epp` router pod to schedule onto the 224-vCPU A4 GPU nodes (where each vLLM worker only requests 32 vCPUs) by patching its tolerations:
> ```bash
> kubectl patch deployment ${GUIDE_NAME}-epp -n ${NAMESPACE} --type='json' -p='[
>   {"op": "add", "path": "/spec/template/spec/tolerations", "value": [
>     {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
>     {"key": "sandbox.gke.io/runtime", "operator": "Exists", "effect": "NoSchedule"}
>   ]}
> ]'
> ```

### 2. Deploy the Aggregated Model Server

Apply the Kustomize overlay for your infrastructure provider (`gke` or `base`):

```bash
export INFRA_PROVIDER=gke # options: base, gke
kubectl apply -n ${NAMESPACE} \
    -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm-deepseek-r1-0528-aggregated/${INFRA_PROVIDER}
```

Wait for both `LeaderWorkerSet` pods (`wide-ep-nvidia-gpu-vllm-decode-0` and `wide-ep-nvidia-gpu-vllm-decode-0-1`) to reach `1/1 Running`:

```bash
kubectl get lws,pods -n ${NAMESPACE} -o wide
```

## Verification

### 1. Get the IP of the Proxy

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send Test Requests

```bash
kubectl run curl-debug --rm -i --restart=Never \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="${NAMESPACE}" -- \
    curl -sS -X POST http://${IP}/v1/completions \
      -H 'Content-Type: application/json' \
      -d "{
          \"model\": \"${MODEL}\",
          \"prompt\": \"How are you today?\",
          \"max_tokens\": 32
      }" | jq
```

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} \
    -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm-deepseek-r1-0528-aggregated/${INFRA_PROVIDER}
```
