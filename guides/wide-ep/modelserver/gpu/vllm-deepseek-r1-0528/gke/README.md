# GKE Overlay

This overlay configures GKE-specific settings for DP-aware WideEP scheduling on H200 nodes with RoCE RDMA networking, allocated through DRA (GKE managed DRANET).

## Summary of GKE-Specific Patches

| Patch | Description |
|---|---|
| DRA resource claims | Requests eight `gke-rdma-template` claims per pod, one GPU+NIC pair per rank matched on PCIe root. |
| GPU node toleration | DRA node pools taint GPU nodes; GPUs allocated through claims do not grant the implicit toleration. |
| Privileged container | Required for GPU-initiated RDMA on GKE. |
| Topology affinity | Prefers same GCE topology block/subblock for prefill and decode pods. |
| `DEEP_EP_DEVICE_TO_HCA_MAPPING` | Maps GPUs to NICs for efficient NVSHMEM NIC selection. |
| `NVSHMEM_DISABLED_GDRCOPY` | Recommended on GKE. |
| Host volumes | GKE-specific hostPath for model and JIT caches. |
| `NCCL_TUNER_PLUGIN` / `NCCL_NET_PLUGIN` | Disables GKE's built-in NCCL tuner and net plugin. |

## Cluster Prerequisites

### DRA node pool

Create a Dataplane V2 cluster and a node pool with `--accelerator-network-profile=auto`,
the DRA node labels, and managed GPU driver install disabled, then install the NVIDIA
driver DaemonSet and the NVIDIA GPU DRA driver. See
[GPU DRA and DRANET on GKE](../../../../../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke).

> [!WARNING]
> Scale node pools created with `--accelerator-network-profile=auto` to zero instead of
> deleting them. Deletion can hang and block new pool creation on the cluster.

### GPU driver

R580 drivers (both `default` and `latest` on GKE 1.36) crash DeepEP high-throughput
kernels with `cudaErrorIllegalAddress`. Use an explicit pre-R580 version with the
[NVIDIA driver installer DaemonSet](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers).
Fallback: set the prefill `--all2all-backend` to `deepep_low_latency` (reduces prefill
throughput).

### Router CPU node

The standalone router pod requests up to 12 CPUs. Provide an `e2-standard-16` or larger
CPU node.
