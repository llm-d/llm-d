# Provisioning GCP Lustre

## Overview

This guide explains how to provision a managed GCP Lustre instance.

## Prerequisites

* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../../../../../prereq/infrastructure/README.md).
* Create a namespace for installation.

```bash
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

## Cluster setup for provisioning a managed GCP Lustre instance (GKE cluster)

To create a new GKE cluster, you need to first set up a separate VPC for provisioning a managed GCP Lustre instance. Follow the [steps from Lustre guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#vpc-setup) to setup a separate VPC network.

If you have an existing cluster, you can update the same VPC network to enable provisioning a managed GCP Lustre instance. Ensure you initialize the variable `NETWORK_NAME` with your existing network name and skip the network creation command in the [setup above](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#vpc-setup).

Please update the `$NETWORK_NAME` and `$LOCATION` variables in [storage_class.yaml](./storage_class.yaml) to match your cluster configuration.

Ensure [Lustre CSI driver is enabled](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#manage) on the cluster, otherwise it would fail to provision a managed GCP Lustre instance.

## Provisioning

### GKE

**1. Create a `StorageClass` for Lustre:**

```bash
cd guides/tiered-prefix-cache/storage/manifests/backend/lustre
kubectl apply -f ./storage_class.yaml -n ${NAMESPACE}
```

**2. Create a PVC:**

Once the `StorageClass` is created, you can create a PVC and mount to your pod like so:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: lustre-pvc
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: 18000Gi # Range from 18000Gi to 954000Gi, must be in multiples of 9000 Gi
  storageClassName: lustre-class
```

### Check the PVC

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`:

```
NAME         STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
lustre-pvc   Bound    pvc-3c793698-XXXXXXX   36000Gi    RWX            lustre-class   <unset>                 6d
```

## Cleanup

```bash
kubectl delete -f ./storage_class.yaml -n ${NAMESPACE}
```