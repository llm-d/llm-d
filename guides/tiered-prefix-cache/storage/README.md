# Offloading Prefix Cache to Shared Storage

## Overview

This guide explains how to offload the vLLM prefix cache (KV cache) to shared storage using the native llm-d FS connector or the LMCache connector. This allows prefix cache reuse across multiple vLLM instances and across nodes that mount the same shared path.

## Default Configuration

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| GPUs per replica (TP)     | 4                                                       |
| GPU Accelerator           | NVIDIA H100                                             |
| CPU Cache Offload Size    | 100 GB                                                  |

### Supported Connectors

| Connector             | Directory                                                              |
| --------------------- | ---------------------------------------------------------------------- |
| llm-d FS Connector    | `modelserver/gpu/vllm/llm-d-fs-connector/`                              |
| LMCache Connector     | `modelserver/gpu/vllm/lmcache-connector/`                              |


<details>
<summary><h4>Aboutllm-d FS Connector</h4></summary>

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

</details>


<details>
<summary><h4>About LMCache Connector</h4></summary>

[LMCache](https://lmcache.ai) is an extension for LLM serving engines that enhances performance by reducing "Time to First Token" (TTFT) and increasing throughput, particularly for long-context scenarios. It provides integration to various storage backends. For more information, visit the [LMCache website](https://lmcache.ai).

</details>

---

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.
- Checkout llm-d repo:

  ```bash
    export branch="main" # branch, tag, or commit hash
    git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

- Set the following environment variables:
  ```bash
    export GAIE_VERSION=v1.5.0
    export GUIDE_NAME="tiered-prefix-cache-storage"
    export NAMESPACE=llm-d-${GUIDE_NAME}
  ```
- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```

- Create a target namespace for the installation:
  ```bash
    kubectl create namespace ${NAMESPACE}
  ```

---

## Installation Instructions

### 1. Prepare a PVC (ReadWriteMany)

Set your storage class depending on your environment:

```bash
export STORAGE_CLASS="" # leave empty to use the cluster default StorageClass; or set "lustre" / "efs-sc"
```

To provision a managed GCP Lustre instance on GKE and configure the corresponding `StorageClass`, follow the [GCP Lustre guide](./manifests/backends/lustre/README.md).

To provision AWS EFS and configure the corresponding `StorageClass`, follow the [EFS guide](./manifests/backends/aws/README.md).


Create a PVC using the selected storage class:

```bash
envsubst < guides/tiered-prefix-cache/storage/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
```

### 2. Deploy the llm-d Router

#### Standalone Mode

This deploys the inference scheduler with an Envoy sidecar side-by-side:

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/tiered-prefix-cache/storage/scheduler/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy instead of standalone:

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../prereq/gateways).
2. _Deploy HTTPRoute and the inference scheduler_:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install llm-d-infpool \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool  \
    -f guides/recipes/scheduler/base.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set experimentalHttpRoute.enabled=true \
    --set experimentalHttpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

</details>

---

### 3. Deploy the Model Server

Apply the Kustomize overlay corresponding to your desired connector backend. 

<details>
<summary><h4>Click here for GCP Lustre</h4></summary>
For GCP lustre, please apply `llm-d-fs-connector-lustre` or `llm-d-fs-connector-lustre` which contains a patch to allow vLLM to write to Lustre.
</details>

```bash
export CONNECTOR=llm-d-fs-connector # llm-d-fs-connector | lmcache-connector | llm-d-fs-connector-lustre | lmcache-connector-lustre
kubectl apply -n ${NAMESPACE} -k guides/tiered-prefix-cache/storage/modelserver/gpu/vllm/${CONNECTOR}
```

---

### 4. (Optional) Enable monitoring

- Install the [Monitoring stack](../../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide:

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
```

---

## Verification

### 1. Check the PVC

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`:

```
NAME         STATUS   VOLUME                  CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
<pvc-name>   Bound    pvc-3c793698-XXXXXXX    18000Gi    RWX            <storage-class>   <unset>              6d
```

### 2. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 3. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-32B",
        "prompt": "How are you today?"
    }' | jq
```
### 4. Verify KV cache is offloaded to storage 

**Send a long prompt (one that crosses several `block_size` boundaries) to trigger offload**

```bash
# Run this inside the interactive shell created in step 3.
# Long prompt (~3K tokens)
PROMPT=$(printf 'Story: '; for i in $(seq 1 800); do printf 'alice met bob and they walked together. '; done)
jq -n --arg prompt "$PROMPT" '{"model":"Qwen/Qwen3-32B", "prompt":$prompt, "max_tokens":3, "temperature":0}' | \
curl -s http://${IP}/v1/completions \
  -H 'Content-Type: application/json' \
  -d @- | jq
```

```bash
# Check the shared PVC for written blocks
POD=$(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ${NAMESPACE} ${POD} -- du -sh /mnt/files-storage/kv-cache
kubectl exec -n ${NAMESPACE} ${POD} -- find /mnt/files-storage/kv-cache -maxdepth 5
```

Expected output: `du -sh` shows hundreds of MB to several GB, and `find` lists a path like
`/mnt/files-storage/kv-cache/<model>/<block-config>/<tp-config>/...` (fs connector) or `/mnt/files-storage/kv-cache/<model>-xxx.pt` (lmcache connector).

If you have monitoring set up, you can also confirm via vLLM's offload metrics in the metrics explorer, `vllm:kv_offload_total_bytes` for fs connector or `lmcache:local_storage_usage` for lmcache connector.

---

## Benchmarking

The following benchmark results demonstrate the performance improvements of offloading the KV cache to Lustre using the LMCache connector. Two scenarios with varying context lengths are provided to illustrate how the performance gains from Lustre scale up as the computational load and KV cache size increase, particularly when exceeding the capacity of local HBM and CPU RAM.

### LMCache connector

#### Benchmark Setup

> [!NOTE]
> The following benchmark results were from a previous release and does not match the deployment of the current release. A follow up benchmark will be conducted and the results will be updated accordingly. See https://github.com/llm-d/llm-d/issues/680.

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

---

## Cleanup

To clean and remove applied deployments:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -f guides/tiered-prefix-cache/storage/manifests/pvc.yaml -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/tiered-prefix-cache/storage/modelserver/gpu/vllm/${CONNECTOR}
kubectl delete namespace ${NAMESPACE}
```
