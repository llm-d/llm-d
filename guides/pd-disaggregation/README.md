# P/D Disaggregation

Prefill/decode (P/D) disaggregation splits inference into separate **prefill** and **decode** pools. Prefill pods process the prompt and produce its KV cache; decode pods pull that KV cache over a KV transfer connector (NIXL by default) and generate the output tokens. The two pools scale independently, so heavy prompts never stall token generation.

For why P/D disaggregation helps, how requests flow between the two pools, and tuning guidance, see [Disaggregated Serving](../../docs/architecture/disaggregation/pd-disaggregation.md).

## Choose a guide

llm-d drives the two phases of a request in one of two ways. Each has its own guide:

| | [Routing sidecar](sidecar/README.md) | [Coordinator](coord/README.md) (experimental) |
| --- | --- | --- |
| Who drives the phases | A routing sidecar in front of each decode engine: the request lands on the decode pod, whose sidecar sends the prompt to the prefill pod and then decodes locally | A standalone Coordinator service runs a `prefill → decode` pipeline per request and calls the router once per phase |
| When the decode pod is picked | Together with the prefill pod, in one scheduling cycle, before prefill starts | After prefill has finished, based on the pool's state at that time |
| Router | One EPP with the `disagg-profile-handler`; Standalone or Gateway Mode | One EPP with the `header-profile-handler`; Gateway Mode only |
| Accelerators and model servers | NVIDIA, AMD, Intel XPU, Google TPU and community accelerators; vLLM and SGLang | vLLM on NVIDIA GPU and TPU7x dynamic sub-slices (TPU not yet run end to end) |
| Maturity | Nightly E2E on several platforms | Community; not covered by nightly E2E |

Both guides deploy the prefill and decode roles as one [`DisaggregatedSet`](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) and use the same prefill and decode scheduling profiles.

**Start with the [routing sidecar](sidecar/README.md)** unless you have a reason to pick the Coordinator. It is the established path, it covers every accelerator, and under uniform or steady load, or when prefill is the bottleneck, the two architectures perform the same.

**Consider the [Coordinator](coord/README.md)** when decode is the contended stage, per-request load is heterogeneous (long-tailed outputs, widely varying prompt or image sizes), and requests arrive in bursts. Its later decode pick then keeps the decode pods balanced: in the [Coordinator Disaggregation benchmarks](../coord-disaggregation/README.md#benchmark) it delivered 4–10% higher throughput and up to 15% lower p99 TTFT in that regime, at the cost of a few milliseconds of TTFT per request for the extra Gateway round trip per phase.

## Prerequisites

Both guides list their full prerequisites; these cluster requirements are shared by both:

- The [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1` or newer with the `DisaggregatedSet` API enabled (`--set enableDisaggregatedSet=true` when installing with Helm). Each guide's environment step installs it.
- Prefill and decode pods exchange KV cache over the network, so place them on nodes connected by a high-bandwidth fabric. NIXL works over TCP, but RDMA networking (InfiniBand, RoCE, EFA) is **highly recommended** for production.

### Platform prerequisites

Each guide selects the platform with `INFRA_PROVIDER`. `base`, `nixl/base`, `moriio/base`, and the TPU and community accelerators need nothing beyond the accelerator's device plugin (see the [sidecar guide's model server tabs](sidecar/README.md#2-deploy-the-model-server)); the other platforms need:

| Accelerator | `INFRA_PROVIDER` | Cluster requirements | Setup |
| --- | --- | --- | --- |
| NVIDIA GPU | `gke`, `gke/a4x`, `gke/a4xmax` | GPU DRA (manual node labels and driver install, not yet GKE-managed) and managed DRANET for RoCE; `gke` targets A3/A4, `gke/a4x` / `gke/a4xmax` target A4X / A4X Max (GB200 / GB300); DRANet must support hairpin (same-node) and cross-rail (inter-node multi-rail) routing | [GKE: GPU DRA and DRANET](../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke), [GKE A4X setup](../../docs/infrastructure/providers/gke/README.md#gke-a4x-gb200-setup) |
| | `coreweave` | An RDMA device plugin exposing `rdma/ib` inside pods (one per pod); a default StorageClass supporting `ReadWriteMany` for the model cache PVC | [CoreWeave](../../docs/infrastructure/providers/coreweave/README.md), [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
| | `cks-mooncake` | vLLM only. GPUs via `nvidia.com/gpu`; InfiniBand devices exposed inside pods via an `rdma/ib` device plugin (NVIDIA Network Operator, Multus with SR-IOV, or equivalent); a vLLM image with `mooncake-transfer-engine` (the standard image may not include it); `VLLM_MOONCAKE_BOOTSTRAP_PORT` (default `8998`) unique per co-located instance. Configures `MooncakeConnector` transport only, not Mooncake Store | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities), [verify RDMA in a pod](../../docs/infrastructure/rdma/README.md#2-inter-pod-network), [Mooncake installation](https://kvcache-ai.github.io/Mooncake/) |
| | `aws` | `p5en.48xlarge` nodes with the EFA device plugin exposing `vpc.amazonaws.com/efa` | [AWS EFA notes](../../docs/infrastructure/rdma/README.md#aws-efa-deprecated---use-upstream-aws-images) |
| AMD GPU | `nixl/oci` | OKE `BM.GPU.MI300X.8` nodes; NVIDIA Network Operator SR-IOV VFs as `nvidia.com/sriov-rdma-vf`, with a `sriov-rdma-vf` SriovNetwork in namespace `default` | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
| | `nixl/tensorwave` | An RDMA device plugin exposing `rdma/ib` inside pods | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
| Intel XPU | `rdma` | A network DRA driver publishing a `dranet-rdma` DeviceClass with RDMA-capable NICs (`dra.net` `rdma` attribute); each claim aligns the NIC with its GPU's PCIe root | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |

The [Coordinator guide](coord/README.md) supports NVIDIA GPU with `base` and `gke`, and TPU7x dynamic sub-slices with `gke`.

### Intel XPU with RDMA

The RDMA overlay ([`modelserver/xpu/vllm/rdma/`](modelserver/xpu/vllm/rdma/)) carries KV-cache transfer on Intel XPU over RDMA instead of TCP. It requires:

- An RDMA DRA driver exposing the `dranet-rdma` device class (e.g., [Intel Network Operator for Kubernetes](https://github.com/intel/network-operator)).
- GPU-NIC PCIe alignment for optimal transfer performance.
- UCX transport configured with `ib,rc,ze_copy`.

The overlay reuses the standard XPU vLLM base and adds one RDMA DRA claim per pod plus RDMA-specific UCX transport settings. Deploy it with `ACCELERATOR_TYPE=xpu` and `INFRA_PROVIDER=rdma` in the [sidecar guide](sidecar/README.md).

## Layout

```
guides/pd-disaggregation/
  modelserver/    model server overlays for every accelerator, shared by both guides
    gpu/vllm/common/                    prefill + decode DisaggregatedSet, no routing sidecar
    gpu/vllm/base/                      common + the routing sidecar on decode (the sidecar guide)
    gpu/vllm/components/routing-sidecar adds the sidecar; moves decode vLLM to port 8200
    gpu/vllm/components/gke             GPU DRA + DRANet RoCE, used by both guides' gke overlays
    tpu/v7-dynamic-slice/vllm/common/   TPU7x sub-slice DisaggregatedSet, no routing sidecar;
                                        base/ adds components/routing-sidecar the same way
  sidecar/        guide.yaml, README.md, router/
  coord/          guide.yaml, README.md, router/, coordinator/,
                  modelserver/gpu/vllm/{base,gke}, modelserver/tpu/v7-dynamic-slice/vllm/gke
                  (thin overlays over the shared common/ directories)
```

Every other accelerator's overlays include the routing sidecar directly and serve the sidecar guide only.
