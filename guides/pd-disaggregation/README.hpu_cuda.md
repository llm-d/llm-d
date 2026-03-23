# Intel Gaudi (HPU) ↔ NVIDIA CUDA Heterogeneous P/D Disaggregation Deployment Guide

## Overview

This document provides complete steps for deploying a heterogeneous PD (Prefill-Decode)
disaggregation service where the **Prefill** phase runs on an Intel Gaudi accelerator
(HPU) and the **Decode** phase runs on an NVIDIA GPU (CUDA). PD disaggregation separates
the prefill and decode phases of inference to enable more efficient resource utilization
and improved throughput.

This configuration allows mixing Intel and NVIDIA hardware in the same inference pipeline,
leveraging NIXL (via the `NixlConnector`) for cross-vendor KV cache transfer over RDMA.


## Architecture

```
  Client Request
       │
       ▼
  ┌──────────┐     KV Cache Transfer (NIXL over RDMA)
  │  EPP /   │ ────────────────────────────────────────────►
  │ Gateway  │        ┌──────────────────┐    ┌──────────────────┐
  └──────────┘        │  Prefill Worker   │    │  Decode Worker   │
                      │  Intel Gaudi(HPU) │    │  NVIDIA GPU(CUDA)│
                      │  kv_buffer: hpu   │    │  kv_buffer: cuda │
                      │  KV layout: NHD   │    │  KV layout: HND  │
                      │  UCX: gaudi_gdr,  │    │  UCX: cuda_copy, │
                      │       rc,ud,ib    │    │       rc,ib      │
                      └──────────────────┘    └──────────────────┘
                           RDMA                        RDMA 
                        (DRANet)                     (DRANet)
```

## Hardware Requirements

- **Prefill Node**: Server with one or more Intel Gaudi2/3 accelerators 
- **Decode Node**: Server with one or more NVIDIA GPUs 
- **Networking**: RDMA capable NICs (InfiniBand or RoCE) on both nodes for KV cache transfer

## Device Management

This setup relies on two Kubernetes mechanisms for hardware device allocation:

### Dynamic Resource Allocation (DRA)

[Kubernetes DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
is used to allocate both Intel Gaudi and NVIDIA GPU devices to the respective pods.

[Gaudi DRA](https://github.com/intel/intel-resource-drivers-for-kubernetes/blob/main/doc/gaudi/USAGE.md) -> Instructions to deploy the Gaudi DRA installation.

[Nvidia DRA](https://github.com/NVIDIA/k8s-dra-driver-gpu) -> Instructions for Nvidia DRA installation. 

- Intel Gaudi device class: `gaudi.intel.com`
- NVIDIA GPU device class: `gpu.nvidia.com`

### DRANet (RDMA Allocation)

[DRANet](https://github.com/kubernetes-sigs/dranet) is used to expose RDMA Devices to pods, enabling high-bandwidth, low-latency KV cache transfer between the heterogeneous prefill and decode workers.

- RDMA device class: `rdma-dranet-pf`

### ResourceClaimTemplates

Two `ResourceClaimTemplate` objects must be applied to your namespace before deploying the stack.

**Prefill — Intel Gaudi + RDMA VF:**

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: intel-1-gaudi-1-rdma
spec:
  spec:
    devices:
      requests:
      - name: gaudi
        exactly:
          deviceClassName: gaudi.intel.com
          count: 1
      - name: rdma-net-interface
        exactly:
          deviceClassName: rdma-dranet-pf
          count: 1
```

**Decode — NVIDIA GPU + RDMA VF:**

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: nvidia-1-gpu-1-rdma
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly:
          deviceClassName: gpu.nvidia.com
          count: 1
      - name: rdma-net-interface
        exactly:
          deviceClassName: rdma-dranet-pf
          count: 1
```

Apply both templates to your namespace:

```bash
kubectl apply -f dra/resourceclaimtemplate/intel-1-gaudi-1-rdma.yaml -n ${NAMESPACE}
kubectl apply -f dra/resourceclaimtemplate/nvidia-1-gpu-1-rdma.yaml -n ${NAMESPACE}
```


## Building Docker Images (Optional)

If you need to build custom vLLM images from source (e.g., to test a specific commit or apply
patches), the `docker/` directory contains the relevant Dockerfiles and the root `Makefile`
provides convenient build targets.

### Available Dockerfiles

| Dockerfile | Purpose | Used by |
|---|---|---|
| `docker/Dockerfile.hpu` | Intel Gaudi (HPU) vLLM image with NIXL | Prefill worker |
| `docker/Dockerfile.cuda` | NVIDIA CUDA vLLM image with NIXL, UCX, NVSHMEM | Decode worker |


### Build the HPU (Prefill) Image

The HPU image clones `vllm-gaudi` and `vllm-project`, installs NIXL, and targets Intel Gaudi2/3:

```bash
# From the llm-d repo root
cd /path/to/llm-d

# Build with defaults (VERSION=v0.2.1, DEVICE=hpu)
make image-build DEVICE=hpu

# Build a specific version
make image-build DEVICE=hpu VERSION=v0.3.0

# Build with a custom registry/base tag
make image-build DEVICE=hpu IMAGE_BASE=my-registry.example.com/llm-d-hpu-dev VERSION=latest
```

The resulting image is tagged as `ghcr.io/llm-d/llm-d-hpu-dev:<VERSION>`.

Key build arguments for `Dockerfile.hpu`:

| ARG | Default | Description |
|---|---|---|
| `DOCKER_URL` | `vault.habana.ai/gaudi-docker` | Habana base image registry |
| `VERSION` | `1.22.0` | Habana software version |
| `BASE_NAME` | `ubuntu22.04` | OS base |
| `PT_VERSION` | `2.7.1` | PyTorch version |

### Build the CUDA (Decode) Image

The CUDA image is a multi-stage build that compiles UCX, NVSHMEM, NIXL, FlashInfer, and vLLM:

```bash
# Build with defaults (VERSION=v0.2.1, DEVICE=cuda)
make image-build DEVICE=cuda

# Build with debug symbols
make image-build DEVICE=cuda BUILD_DEBUG=true

# Build with AWS EFA (Elastic Fabric Adapter) RDMA support
make image-build DEVICE=cuda ENABLE_EFA=true

# Build a production (non-dev) image
make image-build DEVICE=cuda BUILD_TYPE=prod VERSION=v0.3.0
```

The resulting image is tagged as `ghcr.io/llm-d/llm-d-cuda-dev:<VERSION>`.

Key build arguments for `Dockerfile.cuda`:

| ARG | Default | Description |
|---|---|---|
| `CUDA_MAJOR` / `CUDA_MINOR` / `CUDA_PATCH` | `12.9.1` | CUDA version |
| `UCX_VERSION` | `v1.20.0` | UCX transport library version |
| `NIXL_VERSION` | `0.10.0` | NIXL KV transfer library version |
| `FLASHINFER_VERSION` | `v0.6.1` | FlashInfer attention kernel version |
| `ENABLE_EFA` | `false` | Enable AWS EFA RDMA support |
| `BUILD_NIXL_FROM_SOURCE` | `true` | Build NIXL from source vs. pip install |

> **NOTE:** The vLLM commit is pinned in `docker/vllm-version`. To build against a specific
> commit, pass `VLLM_COMMIT_SHA=<sha>` as an additional build arg or edit that file.


### Push Images to Your Registry

After building, retag and push to your internal registry (required for cluster access):

```bash
# Retag the HPU image
make image-retag DEVICE=hpu NEW_TAG=my-custom-tag
docker push ghcr.io/llm-d/llm-d-hpu-dev:my-custom-tag

# Or build and push directly with a custom IMAGE_BASE
make image-build DEVICE=hpu IMAGE_BASE=my-registry.example.com/vllm-hpu VERSION=v0.3.0
make image-push  DEVICE=hpu IMAGE_BASE=my-registry.example.com/vllm-hpu VERSION=v0.3.0

# Same for CUDA decode image
make image-build DEVICE=cuda IMAGE_BASE=my-registry.example.com/vllm-cuda VERSION=v0.3.0
make image-push  DEVICE=cuda IMAGE_BASE=my-registry.example.com/vllm-cuda VERSION=v0.3.0
```

Then update the image references in `ms-pd/values_hpu_cuda.yaml`:

```yaml
prefill:
  containers:
  - name: "vllm"
    image: my-registry.example.com/vllm-hpu:v0.3.0   # custom HPU prefill image

decode:
  containers:
  - name: "vllm"
    image: my-registry.example.com/vllm-cuda:v0.3.0  # custom CUDA decode image
```
## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md)
  to use this guide.
- Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../prereq/infrastructure).
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- Kubernetes v1.31+ with DRA feature gate enabled (`DynamicResourceAllocation=true`).
- Intel Gaudi device plugin / operator deployed on Gaudi nodes.
- NVIDIA GPU operator deployed on NVIDIA nodes.
- DRANet deployed and RDMA SRIOV VFs configured on both node types.
- Create a namespace for installation:

  ```bash
  export NAMESPACE=llm-d-pd  # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN`
  matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token)
  to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)
- Ensure registry pull secret `amr-idc-registry` is available in your namespace for
  pulling the custom vLLM images.

## Installation

### Step 1: Apply ResourceClaimTemplates

Apply the DRA and DRANet `ResourceClaimTemplate` objects (see [Device Management](#device-management) above).

### Step 2: Update Node Selectors

Edit `ms-pd/values_hpu_cuda.yaml` to match the actual hostnames of your Gaudi and NVIDIA nodes:

```yaml
prefill:
  extraConfig:
    nodeSelector:
      kubernetes.io/hostname: <your-gaudi-node-hostname>

decode:
  extraConfig:
    kubernetes.io/hostname: <your-nvidia-node-hostname>
```

### Step 3: Deploy the Stack

Use helmfile to compose and install the stack. The `values_hpu_cuda.yaml` is applied by
default when no specific environment override is used:

```bash
export NAMESPACE=llm-d-pd
cd guides/pd-disaggregation
helmfile apply -e hpu_cuda -n ${NAMESPACE}
```

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release
names and support concurrent installs. Example:

```bash
RELEASE_NAME_POSTFIX=hpu-cuda helmfile apply -n ${NAMESPACE}
```

### Step 4: Install HTTPRoute

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

## NIXL KV Transfer Configuration

NIXL (NixlConnector) is used for cross-vendor KV cache transfer between the HPU prefill
worker and the CUDA decode worker. Key configuration differences between the two roles:

| Parameter | Prefill (HPU) | Decode (CUDA) |
|---|---|---|
| `kv_buffer_device` | `hpu` | `cuda` |
| `VLLM_KV_CACHE_LAYOUT` | `NHD` | `HND` |
| `UCX_TLS` | `gaudi_gdr,rc,ud,ib` | `cuda_copy,rc,ib` |
| `VLLM_NIXL_SIDE_CHANNEL_PORT` | `5574` | `5558` |
| `VLLM_HPU_HETERO_KV_LAYOUT` | `true` | *(not set)* |
| `enable_permute_local_kv` | `True` | `True` |

> **NOTE:** `VLLM_HPU_HETERO_KV_LAYOUT=true` and `kv_buffer_device=hpu` with KV layout `NHD`
> on the HPU side is required for heterogeneous KV layout compatibility with CUDA decode workers.

### UCX Transport Notes

- **Prefill (HPU)**: Uses `gaudi_gdr` for Gaudi Direct RDMA, with `rc` (Reliable Connection)
  and `ib` (InfiniBand) transports.
- **Decode (CUDA)**: Uses `cuda_copy` for GPU memory copy, with `rc` and `ib` transports.
- Set `UCX_MEMTYPE_CACHE=0` on both sides to avoid memory type detection issues.
- Set `UCX_IB_ROCE_REACHABILITY_MODE=all` if using RoCE networking.

## Verify the Installation

Check that all three helm releases are deployed:

```bash
helm list -n ${NAMESPACE}
NAME     NAMESPACE  REVISION  STATUS    CHART
gaie-pd  llm-d-pd   1         deployed  inferencepool-v1.3.1
infra-pd llm-d-pd   1         deployed  llm-d-infra-v1.3.6
ms-pd    llm-d-pd   1         deployed  llm-d-modelservice-v0.4.7
```

Verify all pods are running:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                    READY   STATUS    RESTARTS   AGE
pod/gaie-pd-epp-<hash>                                  1/1     Running   0          2m
pod/ms-pd-llm-d-modelservice-decode-<hash>              2/2     Running   0          2m
pod/ms-pd-llm-d-modelservice-prefill-<hash>             1/1     Running   0          2m
```

Confirm that the ResourceClaims were bound to actual devices:

```bash
kubectl get resourceclaims -n ${NAMESPACE}
```

Expected output shows both HPU+RDMA and GPU+RDMA claims in `Allocated` state.

## Using the Stack

Get the gateway endpoint:

```bash
export ENDPOINT="http://$(kubectl get service infra-pd-inference-gateway-istio \
  -n ${NAMESPACE} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
echo "Using endpoint: $ENDPOINT"
```

List available models:

```bash
curl -s ${ENDPOINT}/v1/models -H "Content-Type: application/json" | jq
```

Run a completion request:

```bash
curl -X POST ${ENDPOINT}/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-0.6B",
    "max_tokens": 64,
    "prompt": "What is prefill-decode disaggregation?"
  }' | jq
```

Verify KV transfer occurred by checking the `kv_transfer_params` in the response — the
`remote_host` and `remote_port` fields confirm a cross-node KV handoff took place.

For more information see [our getting started docs](../../docs/getting-started-inferencing.md).

## Tuning Selective PD

Selective PD enables routing directly to the decode worker (bypassing prefill disaggregation)
for short prompts where the KV transfer overhead exceeds the benefit of disaggregation.
To enable it, update the `threshold` in the GAIE values file:

```bash
cat gaie-pd/values.yaml | yq '.inferenceExtension.pluginsCustomConfig."pd-config.yaml"' \
  | yq '.plugins[] | select(.type == "pd-profile-handler")'
type: pd-profile-handler
parameters:
  threshold: 0  # update this to your desired token count threshold
  hashBlockSize: 5
```

For more information see the [`pd-profile-handler` docs](https://github.com/llm-d/llm-d-inference-scheduler/blob/v0.5.1/docs/architecture.md?plain=1#L205-L210).

## Cleanup

Remove the model services and infrastructure:

```bash
helmfile destroy -n ${NAMESPACE}
```

Or individually:

```bash
helm uninstall ms-pd   -n ${NAMESPACE}
helm uninstall gaie-pd -n ${NAMESPACE}
helm uninstall infra-pd -n ${NAMESPACE}
```

Remove the HTTPRoute:

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```

Remove the ResourceClaimTemplates:

```bash
kubectl delete resourceclaimtemplate intel-1-gaudi-1-rdma-vf -n ${NAMESPACE}
kubectl delete resourceclaimtemplate nvidia-1-gpu-1-rdma-vf  -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names
will differ: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX`, `ms-$RELEASE_NAME_POSTFIX`.

## Customization

For information on customizing a guide and tips to build your own, see
[our docs](../../docs/customizing-a-guide.md).
