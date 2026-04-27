# Provisioning AWS EFS

## Overview

This guide explains how to provision an AWS EFS-backed shared storage for llm-d using the EFS CSI driver.

## Prerequisites

* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../../../../prereq/infrastructure/README.md).
* An AWS account.
* An EKS cluster.
* An existing EFS filesystem.
* Create a namespace for installation.

```bash
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

## Cluster setup for provisioning AWS EFS (EKS cluster)

### Install EFS CSI Driver

Follow the official AWS guide to install the EFS CSI driver:

https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html

Ensure the driver is running in your cluster:

```bash
kubectl get pods -n kube-system | grep efs
```

### Create an EFS File System

Create an EFS filesystem in the same VPC as your EKS cluster and note the `fileSystemId`.

You will need this ID when configuring the `StorageClass`.

## Provisioning

### EKS

**1. Create a `StorageClass` for EFS:**

```bash
cd guides/tiered-prefix-cache/storage/manifests/backends/aws
kubectl apply -f ./storage_class.yaml -n ${NAMESPACE}
```

Update the `fileSystemId` in `storage_class.yaml` before applying.

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
      storage: 1Ti
  storageClassName: efs-sc
```

## Check the PVC

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`:

```
NAME                        STATUS   VOLUME   CAPACITY   ACCESS MODES   STORAGECLASS   AGE
llm-d-kv-cache-storage      Bound    ...      ...        RWX            efs-sc         1m
```

## Performance Recommendations

For best performance with AWS EFS:

- Use **Max I/O** performance mode for higher throughput
- Prefer **Provisioned Throughput** for consistent performance under heavy workloads
- Mount EFS in the same VPC and availability zone as your EKS nodes
- Use multiple vLLM replicas to parallelize I/O
- Monitor throughput and latency using AWS CloudWatch metrics

Note: EFS has higher latency than local SSD or Lustre, and is best suited for shared cache reuse rather than ultra-low latency workloads.


## Cleanup

```bash
kubectl delete -f ./storage_class.yaml -n ${NAMESPACE}
```