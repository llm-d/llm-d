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
| Host volumes | GKE-specific hostPath for model and JIT caches. |
| `NCCL_TUNER_PLUGIN` / `NCCL_NET_PLUGIN` | Disables GKE's built-in NCCL tuner and net plugin. |

## Cluster Prerequisites

### RDMA node pool

Create an RDMA VPC (RoCE network profile, 8 subnets) and attach each subnet to the GPU
node pool with `--additional-node-network`. See the
[GKE AI Hypercomputer docs](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute).

> [!WARNING]
> `--accelerator-network-profile=auto` can wedge node pool deletion. Use explicit
> `--additional-node-network` flags.

### `rdma-0` through `rdma-7` Network objects

Pods in this overlay reference `Network` objects `rdma-0` through `rdma-7` and fail
admission until they exist:

```bash
for i in $(seq 0 7); do
kubectl apply -f - << MANIFEST
apiVersion: networking.gke.io/v1
kind: GKENetworkParamSet
metadata:
  name: rdma-$i
spec:
  vpc: <your RDMA VPC name>
  vpcSubnet: <your RDMA subnet name prefix>-$i
  deviceMode: RDMA
---
apiVersion: networking.gke.io/v1
kind: Network
metadata:
  name: rdma-$i
spec:
  type: Device
  parametersRef:
    group: networking.gke.io
    kind: GKENetworkParamSet
    name: rdma-$i
MANIFEST
done
```

Verify with `kubectl get gkenetworkparamsets` (all Ready).

### GPU driver

R580 drivers (both `default` and `latest` on GKE 1.36) crash DeepEP high-throughput
kernels with `cudaErrorIllegalAddress`. Pin a pre-R580 driver: disable managed driver
install and use the
[NVIDIA driver installer DaemonSet](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers)
with an explicit version. Fallback: set the prefill `--all2all-backend` to
`deepep_low_latency` (reduces prefill throughput).

### Router CPU node

The standalone router pod requests up to 12 CPUs. Provide an `e2-standard-16` or larger
CPU node.
