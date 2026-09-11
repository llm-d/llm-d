# llm-d Accelerators

llm-d supports multiple accelerator vendors and we are expanding our coverage.

## Support

Maintainers for each accelerator type are listed below. See our well-lit path guides for details of deploying on each hardware type.

| Vendor | Models | Maintainers |
| --- | --- | --- |
| AMD | ROCm | Kenny Roche (<Kenny.Roche@amd.com>), Vincent Cave (<Vincent.Cave@amd.com>) |
| CPU | x86_64 | Hongming Zheng (@ZhengHongming888, <hongming.zheng@intel.com>) |
| Google | [TPU](../infrastructure/providers/gke/README.md#llm-d-on-google-kubernetes-engine-gke) | Edwin Hernandez (@Edwinhr716), Cong Liu (@liu-cong, <congliu.thu@gmail.com>) |
| Intel | XPU | Yuan Wu (@yuanwu2017, <yuan.wu@intel.com>) |
| MetaX | C500X GPU | Lianjie Zhang (@lianjiezh, <lianjie.zhang@metax-tech.com>), Mengxuan Li (@archlitchi, <mengxuan.li@dynamia.ai>) |
| NVIDIA | GPU | Will Eaton (<weaton@redhat.com>), Greg (<grpereir@redhat.com>) |
| Rebellions | NPU | Jinmoo Seok (@rebel-jinmoo, <jinmoo_seok@rebellions.ai>), Minwook Ahn (@rebel-minwook, <minwook.ahn@rebellions.ai>), Minho Park (@rebel-minhopark, <minho.park@rebellions.ai>) |

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

## MetaX C500X

MetaX C500X GPUs are supported for community-contributed well-lit paths. The device plugin must expose `metax-tech.com/gpu`. P/D disaggregation uses vLLM `NixlConnector` over TCP; see the [P/D Disaggregation guide](../../guides/pd-disaggregation/README.md) MetaX overlay (`modelserver/metax/vllm/`).

## Rebellions NPU

Rebellions NPUs are supported through `vllm-rbln`, an out-of-tree vLLM platform
plugin that registers on the `vllm.platform_plugins` entry point, shipped in a publicly
pullable container image. The optimized-baseline overlay (`modelserver/npu/vllm/`)
serves `openai/gpt-oss-120b` on two replicas of one NPU each, so the router balances
across two model servers.

**Cluster prerequisites:**

- [RBLN NPU Operator](https://docs.rbln.ai/latest/software/system_management/kubernetes/about_npu_operator.html),
  which installs the driver and the `npu.rebellions.ai` DRA DeviceClass
- Kubernetes 1.34 with the `resource.k8s.io/v1` DRA APIs

**Device allocation.** NPUs are requested through DRA, not an extended resource. One
DeviceClass serves every Rebellions product, so a claim reaches a specific NPU only by
filtering on `productName` in a CEL selector — see
`resource-claim-template.yaml` in the overlay. Omitting the selector lets the claim bind to
a different Rebellions product than the guide was measured on.

**NUMA alignment.** This overlay claims one NPU and no NIC, so it sets no
`resource.kubernetes.io/numaNode` match constraint — such a constraint needs two or more
requests in one claim to bind together.

**Prefix caching is off, so layer the NPU router values.** The runtime disables prefix
caching for sliding-window models, and `openai/gpt-oss-120b` is one: enabling it reports
0 hits over every query. The guide's default scheduling profile is prefix-cache aware, so
apply [`router/npu.values.yaml`](../../guides/optimized-baseline/router/npu.values.yaml)
after the guide's own values to fall back to the load scorer alone. Both the flag and that
file go away once the runtime supports prefix caching here.

**Set `MODEL` when running the guide's steps.** The overlay pins
`openai/gpt-oss-120b`, while the guide defaults `MODEL` to `Qwen/Qwen3-32B`. Export
`MODEL=openai/gpt-oss-120b` so the validation and benchmark steps address the served model.

**Out of scope for this release:** [fast model actuation](../../guides/fast-model-actuation/README.md),
because the runtime does not support sleep and wake; LoRA adapters; and multimodal models,
which on this runtime need a code path that cannot run alongside a KV connector.

**Verification.** The maintainers listed above stand up the optimized-baseline overlay once
per llm-d release tag against the release image and record the result.

## CPU Inferencing

CPU-only inference is supported for deployments without GPU accelerators. This expects 4th Gen Intel Xeon processors (Sapphire Rapids) or later, or equivalent AMD processors. Each replica requires a minimum of 64 CPU cores and 64GB RAM.
