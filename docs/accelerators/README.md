# llm-d Accelerators

llm-d supports multiple accelerator vendors and we are expanding our coverage.

## Support

Maintainers for each accelerator type are listed below. See our well-lit path guides for details of deploying on each hardware type.

| Vendor | Models | Maintainers | Supported Well-lit Paths |
| --- | --- | --- | --- |
| AMD | ROCm | Kenny Roche (<Kenny.Roche@amd.com>), Vincent Cave (<Vincent.Cave@amd.com>) | [optimized baseline](../well-lit-paths/optimized-baseline.md), [Prefill/Decode Disaggregation](../well-lit-paths/pd-disaggregation.md) |
| CPU | x86_64 | Hongming Zheng (@ZhengHongming888, <hongming.zheng@intel.com>) | [optimized baseline](../well-lit-paths/optimized-baseline.md) |
| Google | [TPU](../infra-providers/gke/README.md#llm-d-on-google-kubernetes-engine-gke) | Edwin Hernandez (@Edwinhr716), Cong Liu (@liu-cong, <congliu.thu@gmail.com>) | [optimized baseline](../well-lit-paths/optimized-baseline.md), [Prefill/Decode Disaggregation](../well-lit-paths/pd-disaggregation.md) |
| Intel | XPU | Yuan Wu (@yuanwu2017, <yuan.wu@intel.com>) | [optimized baseline](../well-lit-paths/optimized-baseline.md), [Prefill/Decode Disaggregation](../well-lit-paths/pd-disaggregation.md) |
| Intel | HPU | Sakari Poussa (@poussa, <sakari.poussa@intel.com>) | [optimized baseline](../well-lit-paths/optimized-baseline.md) |
| NVIDIA | GPU | Will Eaton (<weaton@redhat.com>), Greg (<grpereir@redhat.com>) | All |
| Rebellions | NPU | Jinmoo Seok (@rebel-jinmoo, <jinmoo_seok@rebellions.ai>), Minwook Ahn (@rebel-minwook, <minwook.ahn@rebellions.ai>) | [optimized baseline](../well-lit-paths/optimized-baseline.md) |

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

Google Cloud TPUs (v6e, v7) are supported when running on GKE. See the [GKE infrastructure provider docs](../infra-providers/gke/README.md) for cluster setup.

## AMD ROCm

AMD GPUs are supported via ROCm. The specific GPU models supported are determined by the inference container image. See the AMD device plugin or DRA driver below for cluster setup.

## Intel XPU

Intel Data Center GPU Max 1550 and Intel BMG GPUs (Battlemage G21) are supported. Intel XPU deployments use DRA with a unified accelerator type that automatically handles driver selection for both i915 and xe drivers.

For cluster prerequisites, ensure you have the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) installed.

## Intel Gaudi (HPU)

Intel Gaudi 1, Gaudi 2, and Gaudi 3 accelerators are supported via DRA. Ensure you have the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) installed on your cluster before deploying HPU guide variants.

## CPU Inferencing

CPU-only inference is supported for deployments without GPU accelerators. This expects 4th Gen Intel Xeon processors (Sapphire Rapids) or later, or equivalent AMD processors. Each replica requires a minimum of 64 CPU cores and 64GB RAM.

## Accelerator Resource Management

To enable llm-d accelerators to access hardware devices, the devices must be exposed to containers. Kubernetes provides two mechanisms to accomplish this:

1. [Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/)
2. [Dynamic Resource Allocation (DRA)](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)

Typically, clusters use one mechanism or the other to expose accelerator devices. While it's possible to use both mechanisms simultaneously, this requires special configuration not covered in this document.

### Device Plugins

Each vendor provides Device Plugins for their accelerators. The following plugins are available by vendor:

- [AMD ROCm Device Plugin](https://github.com/ROCm/k8s-device-plugin)
- Google TPU Device Plugin (automatically enabled for TPU instances)
- [Intel XPU Device Plugin](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/main/cmd/gpu_plugin.md)
- [Intel Gaudi Device Plugin](https://docs.habana.ai/en/latest/Installation_Guide/Additional_Installation/Kubernetes_Installation/Intel_Gaudi_Kubernetes_Device_Plugin.html)
- [NVIDIA GPU Device Plugin](https://github.com/NVIDIA/k8s-device-plugin)
- [Rebellions NPU Operator](https://docs.rbln.ai/latest/software/system_management/kubernetes/about_npu_operator.html)

### Dynamic Resource Allocation

Each vendor provides DRA resource drivers for their accelerators. The following drivers and setup documentation are available by vendor:

- [AMD ROCm Resource Driver](https://github.com/ROCm/k8s-gpu-dra-driver)
- [Prepare GKE for DRA workloads](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/set-up-dra)
- [Intel XPU and Gaudi Resource Driver](https://github.com/intel/intel-resource-drivers-for-kubernetes)
- [NVIDIA GPU Resource Driver](https://github.com/NVIDIA/k8s-dra-driver-gpu)

Since DRA is a newer Kubernetes feature, some feature gates may be required. Consult your vendor and cluster provider documentation for specific requirements.
