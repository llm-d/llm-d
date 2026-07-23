# GKE Overlay (SGLang Multi-Host LWS)

This overlay configures GKE-specific settings for SGLang multi-host distributed serving via LeaderWorkerSet (LWS) on NVIDIA H100 / H200 / B200 nodes with RoCE RDMA networking.

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| **RDMA resource limits** | Sets legacy non-`.IP` RDMA limits to `0` to work around GKE Warden webhook injecting unavailable resource requests on H200/B200 nodes. |
| **Privileged container** | Required for GPU-initiated RDMA (`libibverbs` / `NVSHMEM`) on GKE. |
| **Topology affinity** | Prefers same GCE topology block/subblock for prefill and decode pods via Kueue TAS. |
| **RDMA network annotations** | Configures multi-NIC RDMA interfaces (`eth2`-`eth9` → `rdma-0` through `rdma-7`). |
| **`NVSHMEM_DISABLED_GDRCOPY`** | Disables GDRCopy trap fallback on virtualized GKE topologies for pure HW RDMA. |
| **Host volumes** | Maps GKE-specific hostPath SSD storage for HuggingFace and SGLang caches (`/mnt/stateful_partition/kube-ephemeral-ssd/shared_disk/`). |
| **`disable-gke-nccl-tuner-patch`** | Disables GKE's built-in NCCL tuner to prevent tuning conflicts with SGLang distributed engines. |
