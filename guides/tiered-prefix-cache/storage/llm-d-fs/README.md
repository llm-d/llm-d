# Offloading Prefix Cache to Shared Storage using llm-d fs-backend

## Overview

This guide shows how to offload the vLLM prefix cache (KV cache) to shared storage using the llm-d file system (FS) backend.
The llm-d FS backend integrates with vLLM's native OffloadingConnector and stores KV blocks on shared storage that exposes
a POSIX-compatible file API (for example IBM Storage Scale, CephFS, GCP Lustre, AWS Lustre).
This enables prefix cache reuse across multiple vLLM instances and across nodes that mount the same shared path.
Key advantages of the FS backend:

* **Fully asynchronous I/O** - Uses vLLM's native offloading pipeline, enabling non-blocking KV cache reads and writes.
* **File system agnostic** - Works with any storage backend that supports standard POSIX file operations.
* **KV sharing across instances and nodes** - Multiple vLLM servers can reuse cached prefixes by accessing the same shared storage path.
* **High throughput via parallelism** - I/O operations are parallelized across multiple threads to increase bandwidth and reduce tail latency.
* **Minimal GPU compute interference** - Uses GPU DMA for data transfers, reducing interference with GPU compute kernels during load and store operations.

**Note:** The storage connector does not handle cleanup or eviction of data on the shared storage.
Storage capacity management must be handled by the underlying storage system or by an external controller.
A simple reference implementation of a PVC-based evictor is available in the [kv-cache repository (PVC Evictor)](https://github.com/llm-d/llm-d-kv-cache), which can be used to automatically clean up old KV-cache files when storage thresholds are exceeded.

## Prerequisites

* Have the [proper client tools installed on your local system](../../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../../prereq/infrastructure/README.md).
* Ensure that a POSIX-compatible shared storage system is available and configured in the cluster. This guide assumes that the required CSI driver is already installed and that a PVC can be created and mounted into vLLM pods.
* Create a namespace for installation.

```bash
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../prereq/client-setup/README.md#huggingface-token) to pull models.
* [Choose an llm-d version](../../../prereq/client-setup/README.md#llm-d-version)

## Installation

```bash
cd guides/tiered-prefix-cache/storage/llm-d-fs
```

### 1. Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../../recipes/gateway/README.md).

### 2. Deploy PVC (shared storage)

The FS backend requires a shared, POSIX-accessible path to store KV-cache files.
This requires a volume that supports `ReadWriteMany (RWX)`.
One common option is a Kubernetes PersistentVolumeClaim (PVC) that is mounted into each vLLM pod.

Apply the PVC manifest:

```bash
kubectl apply -n ${NAMESPACE} -f ./manifests/pvc/pvc.yaml
```

By default, this PVC uses the cluster’s `default` StorageClass (if one is configured).
If your default StorageClass does not support RWX, update the PVC to reference an RWX-capable StorageClass by setting:

```bash
storageClassName: <YOUR_RWX_STORAGECLASS>
```

### 3. Deploy vLLM Model Server

Deploy the vLLM model server with the Offloading Connector enabled and the `llm-d FS backend` configured.

```bash
kubectl apply -k ./manifests/vllm/ -n ${NAMESPACE}
```

**Note:** The llm-d FS backend path must point to the PVC created in the previous step. For additional configuration options [llmd-fs backend configuration](https://github.com/llm-d/llm-d-kv-cache/blob/main/kv_connectors/llmd_fs_backend/README.md)

### 4. Deploy InferencePool

Deploy the `InferencePool` using the [InferencePool recipe](../../../recipes/inferencepool/README.md).

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

## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -f ./manifests/pvc/pvc.yaml -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/offloading-connector -n ${NAMESPACE}
kubectl delete -k ../../../../recipes/gateway/gke-l7-regional-external-managed -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Additional resources

For advanced configuration options and implementation details, see the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).
For end-to-end benchmark results, see our **llm-d FS backend blog** (coming soon).
