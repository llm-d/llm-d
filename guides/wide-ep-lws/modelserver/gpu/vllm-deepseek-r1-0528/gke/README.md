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

This overlay assumes your GKE cluster already has the pieces below. They are one-time
cluster setup, separate from deploying the guide. Tested from scratch on GKE 1.36
(a3-ultragpu-8g, H200) in August 2026.

### 1. A multi-NIC RDMA node pool

The GPU node pool needs the 8 RDMA NICs attached as additional node networks. Follow
the [GKE AI Hypercomputer docs](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute)
to create the RDMA VPC (one VPC with the RoCE network profile and 8 subnets) and pass
each subnet to the node pool with `--additional-node-network`.

> [!WARNING]
> Avoid `--accelerator-network-profile=auto` for now. We have seen GKE fail to tear the
> auto-created networks down, which leaves node pools (and eventually the cluster) stuck
> in a deleting state and blocks creating replacement pools.

### 2. The `rdma-0` through `rdma-7` Network objects

The pod annotations in this overlay reference Kubernetes `Network` objects named
`rdma-0` through `rdma-7`. GKE does not create these for you, and pods are rejected at
admission until they exist. Create one `Network` + `GKENetworkParamSet` pair per RDMA
subnet:

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

Check they are ready with `kubectl get gkenetworkparamsets` (all should show Ready).

### 3. A known-good GPU driver

DeepEP's high-throughput kernels (used by the prefill role) crash with
`cudaErrorIllegalAddress` on the R580 driver series (observed with 580.173.02). Decode's
low-latency kernels are not affected. On GKE 1.36 both `gpu-driver-version=default` and
`latest` install R580, so pin the driver to the version your platform team has validated
for DeepEP (the environments this guide was validated on run pre-R580 drivers) by
disabling managed driver install and using the
[NVIDIA driver installer DaemonSet](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers)
with an explicit version. As a fallback, switching the prefill role's
`--all2all-backend` to `deepep_low_latency` starts and serves correctly on R580, at a
cost to prefill throughput.

### 4. A CPU node big enough for the router

In standalone mode the router pod runs the EPP and an Envoy proxy in one pod, which
together request up to 12 CPUs depending on chart version. Include at least one
`e2-standard-16` (or larger) CPU node or the pod will stay Pending.
