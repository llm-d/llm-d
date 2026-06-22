# GKE Overlay

This overlay configures GKE-specific settings for DP-aware WideEP scheduling on H200 nodes with RoCE RDMA networking.

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| RDMA resource limits | Sets legacy non-`.IP` RDMA limits to `0` to work around GKE Warden webhook injecting unavailable resource requests on H200 nodes. |
| Privileged container | Required for GPU-initiated RDMA on GKE. |
| Topology affinity | Prefers same GCE topology block/subblock for prefill and decode pods. |
| RDMA network annotations | Configures multi-NIC RDMA interfaces (eth2-eth9 → rdma-0 through rdma-7). |
| `DEEP_EP_DEVICE_TO_HCA_MAPPING` | Maps GPUs to NICs for efficient NVSHMEM NIC selection. |
| `NVSHMEM_DISABLED_GDRCOPY` | Recommended on GKE. |
| `HF_HUB_DISABLE_XET` | Disables HF XET when loading model from host storage. |
| Host volumes | GKE-specific hostPath for model and JIT caches. |
| `disable-gke-nccl-tuner-patch` | Disables GKE's built-in NCCL tuner. |
