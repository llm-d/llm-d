# Provisioning Azure Managed Lustre

## Overview

This guide explains how to provision an [Azure Managed Lustre Filesystem (AMLFS)](https://learn.microsoft.com/en-us/azure/azure-managed-lustre/)-backed shared storage for llm-d using the [Azure Lustre CSI driver](https://learn.microsoft.com/en-us/azure/azure-managed-lustre/use-csi-driver-kubernetes).

AMLFS exposes a POSIX-compatible, ReadWriteMany filesystem with the standard Lustre client, supports `O_DIRECT` for GPUDirect Storage, and sustains high throughput per TiB — making it a strong shared storage backend for the llm-d FS connector or the LMCache connector.

## Prerequisites

* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../../../prereq/infrastructure/README.md).
* An Azure subscription with the `Microsoft.StorageCache` resource provider registered.
* An AKS cluster.
* Create a namespace for installation.

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

## Cluster setup for provisioning Azure Managed Lustre (AKS cluster)

### Install the Azure Lustre CSI driver

Follow the official Microsoft guide to install the Azure Lustre CSI driver on AKS:

https://learn.microsoft.com/en-us/azure/azure-managed-lustre/use-csi-driver-kubernetes

Ensure the driver is running in your cluster:

```bash
kubectl get pods -n kube-system | grep azurelustre
```

You should see one `csi-azurelustre-controller-*` Deployment and one `csi-azurelustre-node-*` DaemonSet pod per node intended to mount AMLFS.

### Grant permissions for dynamic provisioning

When using dynamic provisioning, the AMLFS cluster is created on demand by the CSI driver, which acts on behalf of the AKS kubelet identity. Grant the kubelet identity the permissions documented in the [driver parameters guide](https://github.com/kubernetes-sigs/azurelustre-csi-driver/blob/main/docs/driver-parameters.md#permissions-for-kubelet-identity), at minimum:

* `Reader` on the subscription scope.
* `Contributor` on the resource group that will hold the AMLFS cluster.
* `Network Contributor` on the AKS virtual network.

### (Optional) Bring your own AMLFS cluster

If you prefer to provision the AMLFS cluster outside Kubernetes (for example via `az amlfs create` or Bicep/Terraform), follow the [static provisioning section of the driver parameters guide](https://github.com/kubernetes-sigs/azurelustre-csi-driver/blob/main/docs/driver-parameters.md#static-provisioning-bring-your-own-amlfs-cluster-through-aks) to wire the existing AMLFS into a `PersistentVolume` instead of using the `StorageClass` in this guide.

## Provisioning

### AKS

**1. Create a `StorageClass` for Azure Managed Lustre:**

```bash
kubectl apply -f ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/backends/azure/storage_class.yaml -n ${NAMESPACE}
```

Update `sku-name`, `zone`, and the maintenance window in `storage_class.yaml` before applying. The remaining parameters (`location`, `resource-group-name`, `vnet-name`, `subnet-name`) default to the AKS cluster's region, infrastructure resource group and virtual network; override them only if AMLFS must live in a different region, resource group or VNet.

---

**2. Create a PVC:**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: llm-d-kv-cache-storage
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      # AMLFS capacity is provisioned in fixed increments per SKU. Round up to the next
      # valid increment for your chosen sku-name (see AMLFS service documentation).
      storage: 8Ti
  storageClassName: azurelustre-class
```

## Check the PVC

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`:

```
NAME                     STATUS   VOLUME   CAPACITY   ACCESS MODES   STORAGECLASS         AGE
llm-d-kv-cache-storage   Bound    ...      ...        RWX            azurelustre-class    1m
```

Dynamic provisioning of an AMLFS cluster can take several minutes the first time the PVC is bound; the CSI driver creates the underlying Lustre filesystem in your subscription before the PV becomes available.

## Performance Recommendations

For best performance with Azure Managed Lustre:

- Choose an AMLFS SKU that matches your bandwidth requirements: SKUs scale from `AMLFS-Durable-Premium-40` (40 MB/s/TiB) to `AMLFS-Durable-Premium-500` (500 MB/s/TiB).
- Provision AMLFS in the **same region and availability zone** as your AKS GPU nodes to minimize latency.
- Peer the AMLFS VNet/subnet with the AKS VNet, or use the AKS VNet directly, so traffic stays on the Azure backbone.
- Keep the StorageClass `mountOptions` `noatime` set — it reduces metadata traffic on the Lustre MDS.
- Use multiple vLLM replicas to parallelize I/O across the AMLFS cluster.
- Monitor throughput and IOPS on the AMLFS resource in Azure Monitor.

## Choosing an Azure shared storage backend

Azure offers several RWX storage options that can be attached to AKS. The llm-d FS connector ([source](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend)) targets POSIX shared storage and "is suitable for shared storage, as well as a local disk." In practice the backend choice for KV cache offload is driven by:

- **POSIX file API with atomic create/rename semantics** — the connector uses standard `open(2)` / `read(2)` / `write(2)` with `O_DIRECT` and writes blocks via a temp-file-then-rename pattern, not file locking.
- **Cross-pod read-after-write consistency** — a block written by one vLLM pod must be promptly readable by another pod on a different node.
- **Sustained throughput and tail latency** sufficient for KV block reads on the request path (hundreds of MiB per block, multiple GiB/s aggregate).
- **(Optional, for GDS)** `O_DIRECT` support on the mount, when using GPUDirect Storage to bypass the page cache.

| Backend | CSI driver | RWX | Typical use for KV offload |
| ------- | ---------- | --- | -------------------------- |
| **Azure Managed Lustre (AMLFS)** | `azurelustre.csi.azure.com` | Yes | **Recommended.** Real Lustre client, sustains high throughput per TiB, supports `O_DIRECT`. Same backend family the upstream guide already validates (GCP Lustre, AWS FSx for Lustre). |
| Azure NetApp Files (ANF) | NetApp Trident (`csi.trident.netapp.io`) or `file.csi.azure.com` (NFS) | Yes (NFS v3/v4.1) | Viable alternative when AMLFS is unavailable in-region. Pick a Premium or Ultra service level for KV bandwidth; per-volume throughput is capped by service level. |
| Azure Files (NFS v4.1) | `file.csi.azure.com` | Yes (NFS v4.1) | Lower throughput ceiling per storage account; usable for small models or low-concurrency setups but unlikely to scale to multi-pod, multi-GPU KV reuse. Benchmark before relying on it. |
| Azure Blob (NFS v3) | `blob.csi.azure.com` (NFS mode) | Yes (NFS v3) | Object-store latency on the request path is the main concern. May be usable for warm/cold tier patterns; not recommended as the primary KV cache mount. |
| Azure Blob (BlobFuse v2) | `blob.csi.azure.com` (BlobFuse) | Yes (FUSE) | FUSE adds per-call overhead and BlobFuse caching modes do not provide read-after-write consistency across pods on the timescales KV reuse requires. Suitable for model weights/checkpoints, not KV cache. |

If AMLFS is unavailable in your target region, Azure NetApp Files is the best second choice for the llm-d FS connector. Azure Blob variants are best left to model weight or checkpoint storage rather than active KV cache offload.

## Cleanup

```bash
kubectl delete -f ./storage_class.yaml -n ${NAMESPACE}
```

If the `StorageClass` had `reclaimPolicy: Delete` (the default in this guide), deleting the PVC will also delete the underlying AMLFS cluster. To preserve the AMLFS cluster after PVC deletion, change `reclaimPolicy` to `Retain` before binding the PVC.
