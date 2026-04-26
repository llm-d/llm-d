# Offloading Prefix Cache to Shared Storage

## Overview

This guide explains how to offload the vLLM prefix cache (KV cache) to shared storage.

## Prerequisites

* Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.
* Configure and deploy your [Gateway control plane](../../prereq/gateway-provider/README.md).
* Have the [Monitoring stack](../../../docs/monitoring/README.md) installed on your system.
* Create a namespace for installation.

```bash
export NAMESPACE=llm-d-storage # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../../helpers/hf-token.md) to pull models.

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

> [!WARNING]
> `kgateway` is deprecated in llm-d and will be removed in the next release. Prefer `agentgateway` for new self-installed inference deployments, using `guides/recipes/gateway/agentgateway` for recipe-based installs. The legacy `guides/recipes/gateway/kgateway` recipe path is retained only for migration.

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

To provision a managed GCP Lustre instance on GKE and configure the corresponding `StorageClass`, follow the [GCP Lustre guide](./manifests/backends/lustre/README.md).

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

Deploy the `InferencePool` using the [InferencePool recipe](../../recipes/scheduler/README.md).

**NOTE:** This guide uses an InferencePool recipe with HBM cache only. Storage offloading is typically used with CPU offloading, which is not covered, see <https://github.com/llm-d/llm-d/issues/682> for a follow up.

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

### Verify KV cache is offloaded to storage

<!-- TABS:START -->

<!-- TAB:LMCache Connector -->
#### LMCache Connector

You can verify if the KV cache is being offloaded to local storage by checking the metric `lmcache:local_storaqe_usage` through following command.

```
export IP=localhost
export PORT=8000
export POD_NAME=llm-d-model-server-xxxx-xxxx
kubectl exec -it $POD_NAME -- curl -i http://${IP}:${PORT}/metrics | grep lmcache:local_storage_usage
```

Verify the folder size where the Lustre instance is mounted, it should be in GBs after KV cache offloading completes, the actual size will differ based on the requests served.

```
kubectl exec -it $POD_NAME -- du -sh /mnt/files-storage
65G /mnt/files-storage
```

<!-- TABS:END -->

## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -f ./manifests/pvc.yaml -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/<llm-d-fs-connector|lmcache-connector> -n ${NAMESPACE}
# Supported self-installed inference gateway recipe paths are agentgateway (preferred) and kgateway (deprecated migration path).
kubectl delete -k ../../recipes/gateway/<gke-l7-regional-external-managed|istio|agentgateway|agentgateway-openshift|kgateway|kgateway-openshift> -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Benchmarking

The following benchmark results demonstrate the performance improvements of offloading the KV cache to Lustre using the LMCache connector. Two scenarios with varying context lengths are provided to illustrate how the performance gains from Lustre scale up as the computational load and KV cache size increase, particularly when exceeding the capacity of local HBM and CPU RAM.

### LMCache connector

#### Benchmark Setup

* **Hardware:**
  * A total of 16 H100 GPUs, each with 80GB of HBM, were used.
  * The GPUs were distributed across 4 `a3-highgpu-4g` instances, with 4 GPUs per instance.
  * Lustre PVC with storage capacity of 18000GiB

* **vLLM Configuration:**
  * `gpu_memory_utilization` was set to `0.65` to reduce the pressure on the benchmark tool. In production configuration this is typically set to a higher value such as 0.9.
  * Baseline has CPU offloading enabled.
  * Lustre offloading was enabled using Lustre PVC as local backend disk.

* **LMCache Configuration:**
  * For LMCache setup, `LMCACHE_MAX_LOCAL_CPU_SIZE` set to `20GB`, which provides approximately 20*16(number of GPUs)=320GB of CPU RAM cache.
  * Lustre storage capacity available for KV cache offloading was set through `LMCACHE_MAX_LOCAL_DISK_SIZE:"1120Gi"`. As we have 16 GPUs sharing the Lustre disk, 1120*16= 17920Gi <= 18000Gi (i.e. available Lustre capacity) This value can be less than or equal to the available disk size.

The benchmark was conducted using the [inference-perf](https://github.com/kubernetes-sigs/inference-perf) tool with the following hardware, memory, and workload configurations:

* **Workload:**
  * The two different workloads were tested with a constant concurrency of 20 requests with different system_prompt_lengths of 50K and 70K.
  * **Inference Perf configuration**
    * `type`: concurrent
    * `num_requests`: 2700
    * `concurrency_level`: 20
  * **System prompt length: 50K**
    * `num_groups`: 50
    * `system_prompt_len`: 50000
    * `question_len`: 256
    * `output_len`: 1024
    * `num_prompts_per_group`: 50
  * **System prompt length: 70K**
    * `num_groups`: 50
    * `system_prompt_len`: 70000
    * `question_len`: 256
    * `output_len`: 1024
    * `num_prompts_per_group`: 50

* **Memory Calculation:**
  * The KVCache size for the `meta-llama/Llama-3.3-70B-Instruct` model is approximately 320KB per token.
  * With `gpu_memory_utilization` at 0.65, there are 10768 GPU blocks available per engine.
  * The available HBM for KVCache per engine is approximately 55 GB (10768 blocks * 5.12 MB/block).
  * The total available HBM for the KVCache across the entire system was 220 GB (4 engines * 55 GB/engine).
  * Total CPU RAM cache available across the system was 320 GB.
  * Lustre capacity available for KV cache offloading: `LMCACHE_MAX_LOCAL_DISK_SIZE="1120Gi"` for each GPU.

#### Key Findings

In both scenarios, the total KV cache size significantly exceeds the combined capacity of local HBM and CPU RAM. The results demonstrate that as context length and memory demands increase, the performance benefits of offloading to Lustre become even more pronounced.

* **50K system prompt length (KVCache size 994 GiB):** While CPU RAM provides 320GB for KV cache offloading, adding Lustre significantly enhances performance compared to relying on CPU offloading alone.
* **70K system prompt length (KVCache size 1.3 TiB):** As the KV cache footprint grows to 1.3 TiB, the memory pressure intensifies. In this heavier scenario, Lustre delivers even greater performance gains, demonstrating its ability to seamlessly scale with demanding long-context use cases.


#### 50K system prompt length (KVCache size 994 GiB) — KV Cache > (HBM + CPU RAM)

| KVCache > HBM + CPU RAM | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Input Throughput (token per second) | Output Throughput (token per second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 25.38 | 37.74 | 56.21 | 69.69 | 18607 | 354 | 18962 |
| **vLLM + CPU offloading + Lustre** | 20.12 (-21%) | 34.02 (-9.9%) | 45.83 (-18%) | 58.73 (-16%) | 22827 (+23%) | 435 (+23%) | 23262 (+23%) |

#### 70K system prompt length (KVCache size 1.3TiB GiB) — KV Cache >> (HBM + CPU RAM)

| KVCache >> HBM + CPU RAM | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Input Throughput (token per second) | Output Throughput (token per second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 58.02 | 74.75 | 87.99 | 105.46 | 16598 | 226.65 | 16825 |
| **vLLM + CPU offloading + Lustre** | 45 (-22%) | 64.79 (-13%) | 68.28 (-22%) | 87.47 (-17%) | 21364 (+28.71%) | 291 (+28.39%) | 21656 (+28.71%) |





LLM-D FS connector benchmarks coming soon, see tracking issues:
* https://github.com/llm-d/llm-d/issues/680
