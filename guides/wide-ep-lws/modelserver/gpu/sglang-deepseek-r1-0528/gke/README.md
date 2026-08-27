# GKE Overlay (SGLang Multi-Host LWS)

This overlay configures GKE-specific settings for SGLang multi-host distributed serving via LeaderWorkerSet (LWS) on NVIDIA H100 / H200 / B200 nodes with RoCE RDMA networking.

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| **Privileged container** | Grants `privileged: true` required for GPU direct device access and performance on GKE. |
| **Topology affinity** | Configures Kueue TAS topology block/subblock affinity terms for optimal node co-location across prefill and decode groups. |
| **`default-interface` annotation** | Sets `networking.gke.io/default-interface: eth0` for deterministic primary NIC routing. |
| **`NVSHMEM_DISABLED_GDRCOPY`** | Disables GDRCopy trap fallback on virtualized GKE topologies for pure HW RDMA / IPC. |
| **Host SSD volumes** | Maps GKE hostPath SSD storage for HuggingFace and SGLang caches (`/mnt/stateful_partition/kube-ephemeral-ssd/shared_disk/`). |
| **`disable-gke-nccl-tuner-patch`** | Disables GKE's built-in NCCL tuner to prevent tuning conflicts with SGLang distributed engines. |
