# llm-d Accelerators

llm-d supports multiple accelerator vendors and we are expanding our coverage.

## Support

Maintainers for each accelerator type are listed below. See our well-lit path guides for details of deploying on each hardware type.

| Vendor | Models | Maintainers | Supported Well-lit Paths |
| --- | --- | --- | --- |
| AMD | ROCm | Kenny Roche (<Kenny.Roche@amd.com>) | Coming soon |
| Google | [TPU](../infra-providers/gke/README.md#llm-d-on-google-kubernetes-engine-gke) | Edwin Hernandez (@Edwinhr716), Cong Liu (@liu-cong, <congliu.thu@gmail.com>) | [Inference Scheduling](../../guides/inference-scheduling/README.md), [Prefill/Decode Disaggregation](../../guides/pd-disaggregation/README.md) |
| Intel | XPU | Yuan Wu (@yuanwu2017, <yuan.wu@intel.com>) | [Inference Scheduling](../../guides/inference-scheduling/README.md), [Prefill/Decode Disaggregation](../../guides/pd-disaggregation/README.md) |
| NVIDIA | GPU | Will Eaton (<weaton@redhat.com>), Greg (<grpereir@redhat.com>) | All |

## Requirements

We welcome contributions from accelerator vendors. To be referenced as a supported hardware vendor we require at minimum a publicly available container image that launches vLLM in the [recommended configuration](../../guides/prereq/infrastructure/README.md#optional-vllm-container-image).

For integration into the well-lit paths our standard for contribution is higher, **requiring**:

- A named maintainer responsible for keeping guide contents up to date
- Manual or automated verification of the guide deployment for each release

> [!NOTE]
> We aim to increase our requirements to have active CI coverage for all hardware guide variants in a future release.
>
> [!NOTE]
> The community can assist but is not responsible for keeping hardware guide variants updated. We reserve the right to remove stale examples and documentation with regard to hardware support.

## Intel XPU

Intel accelerators are supported via the well-lit paths (see the **Intel** row in the table above). For cluster prerequisites and image expectations, see the [infrastructure prereq](../../guides/prereq/infrastructure/README.md#optional-vllm-container-image).

## Accelerator Resource Management

To enable llm-d accelerators to access hardware devices, the devices must be exposed to containers. Kubernetes provides two mechanisms to accomplish this:

1. [Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/)
2. [Dynamic Resource Allocation (DRA)](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)

Typically, clusters use one mechanism or the other to expose accelerator devices. While it's possible to use both mechanisms simultaneously, this requires special configuration not covered in this document.

### Device Plugins

Each vendor provides Device Plugins for their accelerators. The following plugins are available by vendor:

- [AMD ROCm Device Plugin](https://github.com/ROCm/k8s-device-plugin)
- Google TPU Device Plugin (automatically enabled for TPU instances)
- [Intel XPU Device Plugin](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/main/cmd/gpu_plugin/README.md)
- [Intel Gaudi Device Plugin](https://docs.habana.ai/en/latest/Installation_Guide/Additional_Installation/Kubernetes_Installation/Intel_Gaudi_Kubernetes_Device_Plugin.html)
- [NVIDIA GPU Device Plugin](https://github.com/NVIDIA/k8s-device-plugin)

### Dynamic Resource Allocation

Each vendor provides DRA resource drivers for their accelerators. The following drivers and setup documentation are available by vendor:

- [AMD ROCm Resource Driver](https://github.com/ROCm/k8s-gpu-dra-driver)
- [Prepare GKE for DRA workloads](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/set-up-dra)
- [Intel XPU and Gaudi Resource Driver](https://github.com/intel/intel-resource-drivers-for-kubernetes)
- [NVIDIA GPU Resource Driver](https://github.com/NVIDIA/k8s-dra-driver-gpu)

Since DRA is a newer Kubernetes feature, some feature gates may be required. Consult your vendor and cluster provider documentation for specific requirements.
