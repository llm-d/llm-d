# GKE Overlay (`zai-org/GLM-5.3-Flash` Disaggregated Wide-EP: 2 Prefill + 2 Decode)

This overlay configures GKE-specific settings for DP-aware Wide Expert Parallelism (`DP=16, EP=16, TP=1` per role) serving [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) (320B total / 18B active parameters, 288 routed experts = 18 routed experts per GPU) in **P/D disaggregated mode** (`2 prefill + 2 decode` pods) across **4 × 8-GPU nodes** (e.g., `a4-highgpu-8g` with 32 × NVIDIA B200 GPUs).

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| DRA GPU + RDMA NIC claims (`gke-rdma-template`) | Uses the shared `gke-rdma` component to request eight `gke-rdma-template` DRA claims per pod (each pairing 1 `gpu.nvidia.com` GPU + 1 `mrdma.google.com` RDMA NIC on the same `resource.kubernetes.io/pcieRoot`), removing legacy `nvidia.com/gpu` device-plugin limits/requests. |
| Node tolerations | Tolerates the `nvidia.com/gpu` taint of DRA GPU node pools (GPUs allocated through claims do not grant the implicit toleration). |
| Topology affinity | Prefers the same GCE topology block and subblock (`cloud.google.com/gce-topology-block` / `cloud.google.com/gce-topology-subblock`) within each prefill and decode replica group. |
| `UCX_IB_ROCE_REACHABILITY_MODE` | Cross-rail RoCEv2 reachability (`all`) for NIXL/UCX KV transfers across the GKE DRANET RDMA NIC subnets. |
| Host volumes | Uses `/mnt/stateful_partition/kube-ephemeral-ssd/shared_disk/` for Hugging Face (`hf-cache`) and JIT (`jit-cache`) caches. |
| `NCCL_TUNER_PLUGIN` / `NCCL_NET_PLUGIN` | Disables GKE's built-in NCCL tuner and net plugin. |

## All-to-All Backend

> [!NOTE]
> The base sets `--all2all-backend allgather_reducescatter` as a mitigation, not a tuning choice: NVSHMEM has a known bug on GKE Container-Optimized OS (COS) nodes,
> so this guide does not use the NVSHMEM-based DeepEP backends (`deepep_high_throughput`, `deepep_low_latency`).
> NVSHMEM `3.3.20` to `3.4.5` passes an uninitialized `ibv_ah_attr` to `ibv_create_ah`; the COS kernel validates `static_rate` and returns `EINVAL`,
> and NVSHMEM exits with `Unable to create ah.`
> ([NVIDIA/nvshmem#21](https://github.com/NVIDIA/nvshmem/issues/21), [GKE known issue](../../../../../../../docs/infrastructure/providers/gke/README.md#nvshmem-reports-unable-to-create-ah-on-initialization-for-deepep)).
> NVSHMEM fixed this in `3.5.19`, but the `vllm/vllm-openai` images (`nightly`, which this guide uses, and `v0.31.0`) still ship NVSHMEM `3.4.5`
> and DeepEP `d4f41e4`, whose NVSHMEM kernels also lack the NVSHMEM 3.5 fix [deepseek-ai/DeepEP#564](https://github.com/deepseek-ai/DeepEP/pull/564).
> Without these backends the pods need neither `privileged` nor NVSHMEM/GDRCopy settings.
> Revisit the backend once the vLLM image ships NVSHMEM `3.5.19` or later with a DeepEP build that includes #564.

## Cluster Prerequisites

Provision the GPU node pools with GPU DRA and GKE managed DRANET as described in [GPU DRA and DRANET on GKE](../../../../../../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke). Label all 4 GPU nodes so `gke-managed-networking-dra-driver` publishes `dra.net` `ResourceSlices`:

```bash
kubectl label nodes -l cloud.google.com/gke-accelerator=nvidia-b200 \
  cloud.google.com/gke-networking-dra-driver=true --overwrite
kubectl get resourceslices
```
