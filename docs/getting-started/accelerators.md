# llm-d Accelerators

llm-d supports multiple accelerator vendors and we are expanding our coverage.

## Support

Maintainers for each accelerator type are listed below. See our well-lit path guides for details of deploying on each hardware type.

| Vendor | Models | Maintainers |
| --- | --- | --- |
| AMD | ROCm | Kenny Roche (Kenny.Roche@amd.com), Vincent Cave (Vincent.Cave@amd.com) |
| CPU | x86_64 | Hongming Zheng (@ZhengHongming888, hongming.zheng@intel.com) |
| Google | [TPU](../infrastructure/providers/gke/README.md#llm-d-on-google-kubernetes-engine-gke) | Edwin Hernandez (@Edwinhr716), Cong Liu (@liu-cong, congliu.thu@gmail.com) |
| Intel | XPU | Yuan Wu (@yuanwu2017, yuan.wu@intel.com) |
| NVIDIA | GPU | Will Eaton (weaton@redhat.com), Greg (grpereir@redhat.com) |
| Rebellions | NPU | Jinmoo Seok (@rebel-jinmoo, jinmoo_seok@rebellions.ai), Minwook Ahn (@rebel-minwook, minwook.ahn@rebellions.ai) |

## Requirements

We welcome contributions from accelerator vendors. To be referenced as a supported hardware vendor we require at minimum a publicly available container image that launches vLLM.

For integration into the well-lit paths our standard for contribution is higher, **requiring**:

- A named maintainer responsible for keeping guide contents up to date
- Manual or automated verification of the guide deployment for each release

> [!NOTE]
> We aim to increase our requirements to have active CI coverage for all hardware guide variants in a future release.
>
> [!NOTE]
> The community can assist but is not responsible for keeping hardware guide variants updated. We reserve the right to remove stale examples and documentation with regard to hardware support.

## NVIDIA GPUs

NVIDIA GPUs are the default accelerator for all llm-d guides. Any NVIDIA GPU is supported, with the specific capabilities determined by the inference container image used. No special cluster configuration is required beyond the NVIDIA device plugin or DRA driver.

**CUDA Runtime and Driver Requirements**

llm-d currently ships container images based on the **CUDA 12.9.1** runtime. A future release will move to **CUDA 13.0.2**.

CUDA 12.x and CUDA 13.x have non-overlapping driver compatibility ranges — a given driver version supports one major CUDA family, not both:

| CUDA Version | Minimum Driver | Maximum Driver |
|---|---|---|
| CUDA 12.9.1 (current) | 525.60.13 | < 580 |
| CUDA 13.0.2 (planned) | 580.65.06 | N/A |

> **Recommended driver version: 575.x** for current llm-d releases using CUDA 12.9.1. This provides the latest features and fixes within the CUDA 12.x compatible driver range.
>
> When llm-d moves to CUDA 13.0.2, the minimum driver version will become **580.65.06**. Users should plan to upgrade their node drivers to 580+ ahead of this transition.

For the full CUDA/driver compatibility matrix, see the [CUDA Toolkit Release Notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html).

## Google TPU

Google Cloud TPUs (v6e, v7) are supported when running on GKE. See the [GKE infrastructure provider docs](../infrastructure/providers/gke/README.md) for cluster setup.

## AMD ROCm

AMD GPUs are supported via ROCm. The specific GPU models supported are determined by the inference container image. See the AMD device plugin or DRA driver below for cluster setup.

## Intel XPU

Intel Data Center GPU Max 1550 and Intel BMG GPUs (Battlemage G21) are supported. Intel XPU deployments use DRA with a unified accelerator type that automatically handles driver selection for both i915 and xe drivers.

For cluster prerequisites, ensure you have the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) installed.

### XPU with RDMA

For P/D disaggregation with RDMA-accelerated KV-cache transfer on Intel XPU, the following additional prerequisites apply:

- An RDMA DRA driver exposing the `dranet-rdma` device class (e.g., [Intel Network Operator for Kubernetes](https://github.com/intel/network-operator)).
- GPU-NIC PCIe alignment for optimal transfer performance.
- UCX transport configured with `ib,rc,ze_copy`.

The RDMA overlay (`modelserver/xpu/vllm-rdma/`) reuses the standard XPU vLLM base and adds one RDMA DRA claim per pod plus RDMA-specific UCX transport settings. See the [P/D Disaggregation guide](../../guides/pd-disaggregation/README.md) for deployment instructions.

## CPU Inferencing

CPU-only inference is supported for deployments without GPU accelerators. This expects 4th Gen Intel Xeon processors (Sapphire Rapids) or later, or equivalent AMD processors. Each replica requires a minimum of 64 CPU cores and 64GB RAM.

