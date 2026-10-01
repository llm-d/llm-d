# GKE Overlay (`zai-org/GLM-5.3-Flash` Aggregated Wide-EP)

This overlay configures GKE-specific settings for DP-aware Wide Expert Parallelism (`DP=16, EP=16, TP=1`) serving [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) (320B total / 18B active parameters, 288 routed experts = 18 routed experts per GPU) in **aggregated mode** across **2 × 8-GPU nodes** (e.g., `a4-highgpu-8g` with 16 × NVIDIA B200 GPUs).

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| DRANET RDMA NIC claims | Requests eight `gke-rdma-nic-template` (`mrdma.google.com`) claims per pod (`gpu0rdma0`–`gpu7rdma0`), while GPUs (`nvidia.com/gpu: "8"`) are allocated via the GKE NVIDIA GPU device plugin. |
| Node tolerations | Tolerates `nvidia.com/gpu` and `sandbox.gke.io/runtime` taints on GPU node pools. |
| Privileged container | Required for GPU-initiated RDMA (NVSHMEM IBGDA) on GKE. |
| Topology affinity | Prefers the same GCE topology block and subblock (`cloud.google.com/gce-topology-block` / `cloud.google.com/gce-topology-subblock`) for the 2 worker pods. |
| `DEEP_EP_DEVICE_TO_HCA_MAPPING` | Maps each GPU index (`0..7`) to its paired Mellanox HCA (`mlx5_0..mlx5_7`). |
| `NVSHMEM_DISABLED_GDRCOPY` | Disables GDRCopy in favor of GPU-initiated IBGDA RDMA on GKE. |
| Host volumes | Uses `/mnt/stateful_partition/kube-ephemeral-ssd/shared_disk/` for Hugging Face (`hf-cache`) and JIT (`jit-cache`) caches. |
| `NCCL_TUNER_PLUGIN` / `NCCL_NET_PLUGIN` | Disables GKE's built-in NCCL tuner and net plugin via the `disable-gke-nccl-tuner-patch` component. |
