# [In Development] Offloading Prefix Cache to Shared Storage

## Overview

This guide explains how to offload the vLLM prefix cache (KV cache) to shared storage.

## Prerequisites

* Have the [proper client tools installed on your local system](../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../prereq/infrastructure/README.md).
* Create a namespace for installation.

```bash
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../prereq/client-setup/README.md#huggingface-token) to pull models.
* [Choose an llm-d version](../../prereq/client-setup/README.md#llm-d-version)

## Storage Connectors

<!-- TABS:START -->

<!-- TAB:llm-d FS Connector -->

### llm-d FS Connector

The **llm-d FS connector** integrates with vLLM's native OffloadingConnector and stores KV blocks on shared storage that exposes a POSIX-compatible file API (for example IBM Storage Scale, CephFS, GCP Lustre, AWS Lustre).

This enables prefix cache reuse across multiple vLLM instances and across nodes that mount the same shared path.

**Key advantages:**

* **Fully asynchronous I/O** - Uses vLLM's native offloading pipeline, enabling non-blocking KV cache reads and writes.
* **File system agnostic** - Works with any storage backend that supports standard POSIX file operations.
* **KV sharing across instances and nodes** - Multiple vLLM servers can reuse cached prefixes by accessing the same shared storage path.
* **High throughput via parallelism** - I/O operations are parallelized across multiple threads to increase bandwidth and reduce tail latency.
* **Minimal GPU compute interference** - Uses GPU DMA for data transfers, reducing interference with GPU compute kernels during load and store operations.

**Note:** The storage connector does not handle cleanup or eviction of data on the shared storage. Storage capacity management must be handled by the underlying storage system or by an external controller. A simple reference implementation of a PVC-based evictor is available in the [kv-cache repository (PVC Evictor)](https://github.com/llm-d/llm-d-kv-cache), which can be used to automatically clean up old KV-cache files when storage thresholds are exceeded.

For advanced configuration options and implementation details, see the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

<!-- TAB:LMCache Connector -->

### LMCache Connector

[LMCache](https://lmcache.ai) is an extension for LLM serving engines that enhances performance by reducing "Time to First Token" (TTFT) and increasing throughput, particularly for long-context scenarios. It provides integration to various storage backends. For more information, visit the [LMCache website](https://lmcache.ai).

<!-- TABS:END -->

## Installation

```bash
cd guides/tiered-prefix-cache/storage
```

### 1. Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../recipes/gateway/README.md).


### 2. Prepare a PVC

#### 2.1 Provision the Storage Backend

If your cluster admin has already configured a storage class, you can set the `STORAGE_CLASS` variable and skip the following steps.

```
export STORAGE_CLASS=<your pvc storage class>
```

The following provides instructions to configure different storage backends, and set the `STORAGE_CLASS` variable accordingly.

<!-- TABS:START -->

<!-- TAB:Default Storage Class -->

#### Default Storage Class

If your cluster admin has already set up a `default` storage class:

```
export STORAGE_CLASS=default
```

No additional provision steps are required.

<!-- TAB:GCP Lustre -->

#### GCP Lustre

Set your storage class which will be used later to provision the PVC.

```
export STORAGE_CLASS=lustre
```

To provision a managed GCP Lustre instance on GKE and configure the correspoinding `StorageClass`, follow the [GCP Lustre guide](./manifests/backends/lustre/README.md).

<!-- TABS:END -->


#### 2.2. Create the PVC

This guide requires a shared, POSIX-accessible path to store KV-cache files. This requires a volume that supports ReadWriteMany (RWX). One common option is a Kubernetes PersistentVolumeClaim (PVC) that is mounted into each vLLM pod.

Create a PVC using the `$STORAGE_CLASS` storage class set above. 

```bash
kubectl apply -f ./manifests/pvc.yaml -n ${NAMESPACE}
```

### 3. Deploy vLLM with Storage Connector

Choose a connector to offload prefix cache.

<!-- TABS:START -->

<!-- TAB:llm-d FS Connector -->

#### llm-d FS Connector

```bash
kubectl apply -k ./manifests/vllm/llm-d-fs-connector -n ${NAMESPACE}
```

<!-- TAB:LMCache Connector -->

#### LMCache Connector


```bash
kubectl apply -k ./manifests/vllm/lmcache-connector -n ${NAMESPACE}
```


<!-- TABS:END -->

### 4. Deploy InferencePool

<!-- TABS:START -->

<!-- TAB:llm-d FS Connector -->

#### llm-d FS Connector

Deploy the `InferencePool` using the [InferencePool recipe](../../../recipes/inferencepool/README.md).

**NOTE:** This guide uses an InferencePool recipe with HBM cache only. Storage offloading is typically used with CPU offloading, which is not covered, see https://github.com/llm-d/llm-d/issues/682 for a follow up.

<!-- TAB:LMCache Connector -->

#### LMCache Connector

This guide currently uses the same tired prefix caching scoring configuration, so deploy the inferencepool following [CPU offloading inferencepool guide](../cpu/README.md#deploy-inferencepool). A follow up is to further optimize `inferencepool` configuration considering the storage tier.

<!-- TABS:END -->


## Verifying the installation

You can verify the installation by checking the status of the created resources.

### Check the Gateway

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`.

```bash
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         16m
```

### Check the HTTPRoute

```bash
kubectl get httproute -n ${NAMESPACE}
```

```bash
NAME          HOSTNAMES   AGE
llm-d-route               17m
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

### Check the InferencePool

```bash
kubectl get inferencepool -n ${NAMESPACE}
```

```bash
NAME            AGE
llm-d-infpool   16m
```

### Check the Pods

```bash
kubectl get pods -n ${NAMESPACE}
```

You should see the InferencePool's endpoint pod and the model server pods in a `Running` state.

```bash
NAME                                  READY   STATUS    RESTARTS   AGE
llm-d-infpool-epp-xxxxxxxx-xxxxx     1/1     Running   0          16m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
```

### Verify Cache Loading from Storage

<!-- TABS:START -->

<!-- TAB:LMCache Connector -->
#### LMCache Connector

You can verify if the requests are being served from local storage by check the metric `lmcache:local_storaqe_usage` through following command.

```
export IP=localhost
export PORT=8000
kubectl exec -it llm-d-model-server-xxxx-xxxx -- curl -i http://${IP}:${PORT}/metrics | grep lmcache:local_storage_usage
```

<!-- TABS:END -->


## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -f ./manifests/pvc.yaml -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/<llm-d-fs-connector|lmcache-connector> -n ${NAMESPACE}
kubectl delete -k ../recipes/gateway/<gke-l7-regional-external-managed|istio|kgateway|kgateway-openshift> -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Benchmarking

Coming soon, see tracking issues:
* https://github.com/llm-d/llm-d/issues/680
* https://github.com/llm-d/llm-d/issues/681