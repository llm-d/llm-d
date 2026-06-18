# KV Cache Offloading

[![E2E (GKE GPU Native)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml)
[![E2E (GKE GPU LMcache)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml)

## Overview

Efficient caching of prefix computation states to avoid recomputation is crucial for boosting Large Language Model (LLM) inference performance such as Time to First Token (TTFT) and overall throughput, as well as reducing the cost.
For the self-attention mechanism, the generation of the next token leverages the prefix Key & Value (KV) tensors.
For State Space Model (SSM) models such as mamba models, reusing cache of its SSM states of prefix locations also saves computation for the next token.
In this guide we use the term "prefix cache" to refer to the cache of computation states in the prefix tokens of a target token which includes the caching of prefix KV tensors and other forms of caches.
The prefix aware request scheduling optimizations in the [optimized baseline](../optimized-baseline/README.md) also applies here.

State of the art inference engines already implement native prefix cache reuse across requests in accelerator High-Bandwidth Memory (HBM), but in most serving environments HBM is already a constrained resource. To increase the amount of available memory beyond HBM requires more cache storage, driving the need for offloading prefix cache from HBM to more cost effective storage options such as CPU RAM.

This well-lit path offers multiple sub-guides per the cache storage type, either used standalone, or combined with other storage types in a tiered cache hierarchy. It also provides high level guidance on their suitability per workload, and makes recommendations about selecting and configuring a prefix cache offloading implementation.

## Storage Types

### CPU RAM

Enabling prefix cache offloading to CPU is recommended for the following reasons:

* Little operational overhead.
* There are usually more CPU RAM storage available than accelerator HBM on the host offering much larger cache capacity.
* CPU - accelerator transfer is faster than recomputation for most cases.
* (WIP) Prefix cache storage tier aware optimized baseline makes smart decisions based on cache tier (accelerator HBM vs. CPU RAM).

In low cache size scenario where HBM is primarily used, async CPU offloading should incur little overhead. In high cache size scenario loading cache from CPU RAM offers significantly higher cache hit and thus better performance than HBM only.

### Local Disk

Utilizing local disk storage can significantly increase the cache capacity. However disks are typically significantly slower than CPU RAM.

Consider this when:

* your workload can tolerate the latency overhead.
* the cache capacity of local disks is sufficient for your use case.

Otherwise we recommend a shared storage because it offers cache sharing between instances, has more options to choose from to get a good tradeoff between cost and performance, and offers significantly larger capacity.

### Shared Storage

Offloading prefix cache to a shared (remote) storage tier provides several important benefits beyond local CPU or disk caching:

* **Extended cache capacity** - Offers massive storage capacity that is independent of the inference engine deployment size.
* **Shared KV-cache across nodes** - Multiple inference replicas can access and reuse the same prefix cache.
* **Fast scale-up** - New nodes can immediately reuse existing KV-cache data without warming the cache from scratch.
* **Persistence across restarts or failures** - KV-cache data survives pod restarts, rescheduling, and node failures.
* **Enterprise storage integration** - Can leverage mature enterprise storage systems (for example CephFS, GCP Lustre, IBM Storage Scale) with built-in durability, monitoring, and access control.

However, shared storage introduces additional operational and performance considerations. Latency and throughput depend on the characteristics of the underlying storage system, so careful evaluation is required to ensure that cache transfer overhead does not negatively impact inference performance.

Integration between the storage system and llm-d is achieved through vLLM connectors. The specific connector and data path depend on the storage system type and the underlying transport mechanism. Any storage connector that is compatible with vLLM can be used **transparently within the llm-d project**.

### P2P Cache Sharing

A P2P network can be formed between the inference engine instances to share caches in HBMs or CPU memory. It enables more cache sharing without needing additional storage resources. However this strategy adds operational overhead, and potential contention between model parallelism traffic such as tensor parallelism. We will add more recommendations in the following releases.

## Cache Tiering

Generally multiple cache tiers can be applied ordered by their cache read/write latencies, allowing frequently accessed caches to stay as close as possible to the accelerator, and large or less frequently accessed caches to be offloaded to slower tiers. We recommend always setting up HBM and CPU RAM tiers, and consider a third or fourth tier when your cache needs goes beyond HBM + CPU RAM.

---

## Supported Connectors

| Connector | Storage Tier | Directory |
| --------- | ------------ | --------- |
| vLLM Native OffloadingConnector | CPU RAM | `modelserver/gpu/vllm/native/cpu/` |
| vLLM Native OffloadingConnector | CPU RAM + Filesystem (shared storage) | `modelserver/gpu/vllm/native/fs/` |
| LMCache Connector | CPU RAM | `modelserver/gpu/vllm/lmcache-connector/cpu/` |
| LMCache Connector | Filesystem (shared storage) | `modelserver/gpu/vllm/lmcache-connector/fs/` |
| TPU KVCache Connector | CPU RAM | `modelserver/tpu/v6/vllm/native/cpu/` and `modelserver/tpu/v7/vllm/native/cpu/` |
| SGLang HiCache | CPU RAM | `modelserver/gpu/sglang/native/cpu/` |

<details>
<summary><h4>About vLLM Native OffloadingConnector</h4></summary>

The vLLM native OffloadingConnector offloads KV blocks to CPU RAM and optionally to a POSIX filesystem (for example IBM Storage Scale, CephFS, GCP Lustre, AWS EFS), enabling a multi-tier cache hierarchy: HBM → CPU RAM → shared storage.

**Key advantages:**

* **Fully asynchronous I/O** - Uses vLLM's native offloading pipeline, enabling non-blocking KV cache reads and writes.
* **File system agnostic** - Works with any storage backend that supports standard POSIX file operations.
* **KV sharing across instances and nodes** - Multiple vLLM servers can reuse cached prefixes by accessing the same shared storage path.
* **High throughput via parallelism** - I/O operations are parallelized across multiple threads to increase bandwidth and reduce tail latency.
* **Minimal GPU compute interference** - Uses GPU DMA for data transfers, reducing interference with GPU compute kernels during load and store operations.

**Note:** The storage connector does not handle cleanup or eviction of data on the shared storage. Storage capacity management must be handled by the underlying storage system or by an external controller. A simple reference implementation of a PVC-based evictor is available in the [kv-cache repository (PVC Evictor)](https://github.com/llm-d/llm-d-kv-cache).

For advanced configuration options and implementation details, see the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

</details>

<details>
<summary><h4>About LMCache Connector</h4></summary>

[LMCache](https://lmcache.ai) is an extension for LLM serving engines that enhances performance by reducing "Time to First Token" (TTFT) and increasing throughput, particularly for long-context scenarios. It provides integration to various storage backends including CPU RAM and shared filesystems. For more information, visit the [LMCache website](https://lmcache.ai).

</details>

<details>
<summary><h4>About SGLang HiCache</h4></summary>

[HiCache](https://github.com/sgl-project/sglang) is the implementation of CPU prefix cache offloading in SGLang. It enables large-scale prefix caching by offloading KV cache blocks to CPU RAM, significantly increasing the effective cache capacity beyond available GPU HBM.

</details>

---

## Default Configuration

### GPU

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| GPUs per replica (TP)     | 2                                                       |
| GPU Accelerator           | NVIDIA H100                                             |
| CPU Cache Offload Size    | 100 GB                                                  |

### TPU

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| TPUs per replica (TP)     | 8                                                       |
| TPU Accelerator           | TPU v7                                                  |
| HBM Staging Buffer Size   | 1000 Blocks (~34 GB)                                    |
| CPU Cache Offload Size    | 25000 Chunks (~780 GB)                                  |


## Additional Configuration

### GPU 

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) |
| GPUs per replica (TP)     | 1                                                       |
| GPU Accelerator           | NVIDIA H100                                             |
| CPU Cache Offload Size    | 100 GB          

This guide supports both GPU and TPU. The Kustomize overlays are available in `modelserver/gpu/vllm/` and `modelserver/tpu/` (supporting both v6 and v7).

---

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

* Set the following environment variables:

  ```bash
  export GAIE_VERSION=v1.5.0
  export ROUTER_CHART_VERSION=v0
  export NAMESPACE=llm-d-tiered-prefix-cache
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
  ```

* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.
<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

```bash
helm install tiered-prefix-cache \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. _Deploy the llm-d Router and an HTTPRoute_:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install tiered-prefix-cache \
    oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

---

### 2. Deploy the Model Server

Select the connector and infrastructure provider matching your environment:

**For NVIDIA GPU — CPU offloading only:**

```bash
export MODEL_SERVER=vllm # vllm | sglang
export CONNECTOR=native  # native | lmcache-connector
export VARIANT=cpu       # cpu
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/${MODEL_SERVER}/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER}/
```

**For NVIDIA GPU — filesystem (shared storage) tier:**

If using the `native/fs/` or `lmcache-connector/fs/` variants, create a ReadWriteMany PVC.

To provision a managed GCP Lustre instance on GKE and configure the corresponding `StorageClass`, follow the [GCP Lustre guide](./manifests/backends/lustre/README.md).

To provision AWS EFS and configure the corresponding `StorageClass`, follow the [EFS guide](./manifests/backends/aws/README.md).

```bash
export STORAGE_CLASS="" # set your preferred storage class or leave empty to use cluster default; or set "lustre" / "efs-sc"
envsubst < ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
```

```bash
export CONNECTOR=native  # native | lmcache-connector
export VARIANT=cpu        # cpu | fs
export INFRA_PROVIDER=base  # base | gke
export ACCELERATOR=gpu # gpu | tpu/v6 | tpu/v7
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/${ACCELERATOR}/vllm/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER}/
```

> [!NOTE]
> To enable tiered prefix caching, we customize the llm-d EPP configuration. We configure two prefix cache scorers: one for the GPU/TPU cache and another for the CPU cache.
> LRU capacity for the CPU cache must be manually configured (`lruCapacityPerServer`) because vLLM currently does not emit CPU block metrics.

---

### 3. (Optional) Enable monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* Deploy the monitoring resources for this guide:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```

---

## Verification

### 1. Check the PVC (filesystem variants only)

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`.

### 2. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service tiered-prefix-cache-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

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

### 4. Verify KV cache is offloaded to storage (filesystem variants)

```bash
# Long prompt (~3K tokens) to trigger offload
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
`/mnt/files-storage/<model>_<hash>_r0/<block-config>/<tp-config>/...` (native fs connector) or `/mnt/files-storage/kv-cache/<model>-xxx.pt` (lmcache connector).

If you have monitoring set up, confirm via `vllm:kv_offload_total_bytes` (native) or `lmcache:local_storage_usage` (lmcache) in the metrics explorer.

---

## Cleanup

```bash
helm uninstall tiered-prefix-cache -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/sglang/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER} --ignore-not-found
kubectl delete -f ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml -n ${NAMESPACE}  # if PVC was created
kubectl delete namespace ${NAMESPACE}
```

---

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a shared-prefix synthetic workload designed to exercise tiered cache eviction behavior against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **tiered-prefix-cache-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to exercise tiered cache eviction across HBM and CPU RAM — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

### 1. Install the `llmdbenchmark` CLI

Automatically clone the benchmark repository into `./llm-d-benchmark/` and create a virtualenv at `./llm-d-benchmark/.venv/` containing dependencies and its installation:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
```

Activate the `venv` and enter the repository directory - both are required: the `venv` puts `llmdbenchmark` on your PATH, and the repository directory contains the `workload/profiles/` and `config/specification/` files that orchestrate the benchmark:

```bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```

> [!NOTE]
> Subsequent `llmdbenchmark` commands in this section assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the two commands above.

### 2. Resolve the endpoint of the stack you just deployed

Set two variables so the rest of the section is topology-agnostic: the endpoint URL and the gateway class. The gateway class tells the CLI which deployment topology the cluster is actually running, without this, the CLI re-renders against the benchmark scenario's default values.

**Standalone Mode** (the default in this guide — no Kubernetes Gateway, EPP pod with an Envoy sidecar):

```bash
export ENDPOINT_URL="http://$(kubectl get service tiered-prefix-cache-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly # standalone mode
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"

# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

</details>

### 3. Run the benchmark profile for Tiered Prefix Cache

`guide_tiered-prefix-cache_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the load profile used to generate the [results below](#cpu-offloading-benchmarking-results) (250 prefix groups × 5 prompts each on a 60-second Poisson interval) and is shaped to highlight the strengths of tiered prefix-cache offloading by exercising eviction across HBM and CPU RAM.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/tiered-prefix-cache \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "Qwen/Qwen3-32B" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_tiered-prefix-cache_1.yaml \
    --analyze
```

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.

---

### CPU Offloading Benchmarking Results

The current weight configuration defaults to `1:1:1:1:1` (Queue Scorer : KV Cache Utilization Scorer : GPU Prefix Cache Scorer : CPU Prefix Cache Scorer : LRU Scorer).

#### GPU — High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

The benchmark runs on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2) using Qwen3-32B.

* **Workload**: 250 prefix groups, 5 prompts per group, system prompt length of 16,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **GPU Cache Size (Total)**: 2,384,000 tokens (381 GB / 355 GiB).
* **CPU Cache Size (Total)**: 10,496,000 tokens (~1,718 GB / 1,600 GiB) at 200 GiB/replica.
* **Workload Unique Cache (Working Set)**: 4,640,000 tokens (~760 GB / 708 GiB).

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **5.0 QPS** | **Optimized Baseline (HBM-only)** | 1.62 | 2.65 | 11.98 | 21.60 | 74,638.5 |
| | **GPU + CPU tier prefix aware routing** | 1.17 (-27.8%) | 1.79 (-32.5%) | 11.49 (-4.1%) | 20.25 (-6.3%) | 82,880.0 (+11.0%) |
| **10.0 QPS** | **Optimized Baseline (HBM-only)** | 8.08 | 16.61 | 26.28 | 32.60 | 122,387.7 |
| | **GPU + CPU tier prefix aware routing** | 0.41 (-94.9%) | 1.31 (-92.1%) | 6.97 (-73.5%) | 9.23 (-71.7%) | 167,027.5 (+36.5%) |
| **20.0 QPS** | **Optimized Baseline (HBM-only)** | 43.67 | 78.13 | 62.29 | 92.14 | 114,663.2 |
| | **GPU + CPU tier prefix aware routing** | 0.89 (-98.0%) | 2.66 (-96.6%) | 9.03 (-85.5%) | 11.22 (-87.8%) | 300,749.2 (+162.3%) |
| **40.0 QPS** | **Optimized Baseline (HBM-only)** | 115.57 | 206.39 | 134.78 | 223.93 | 115,645.0 |
| | **GPU + CPU tier prefix aware routing** | 25.38 (-78.0%) | 48.14 (-76.7%) | 33.95 (-74.8%) | 56.29 (-74.9%) | 331,212.6 (+186.4%) |

#### TPU — High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

| Medium Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Overall Throughput (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.98 | 2.1 | 22.1 | 26.2 | 67262.3 |
| **vLLM + CPU offloading 25000 Chunks** | 0.56 (-49%) | 0.5 (-75.7%) | 20.3 (-8.1%) | 23.6 (-9.9%) | 73178.1 (+8.9%) |

---

### Storage Offloading Benchmarking Results

> [!NOTE]
> The following benchmark results were from a previous release and do not match the deployment of the current release. A follow up benchmark will be conducted and the results will be updated accordingly. See <https://github.com/llm-d/llm-d/issues/680>.

#### LMCache Connector + Lustre

LMCache configuration: `LMCACHE_MAX_LOCAL_CPU_SIZE=20GB`, `LMCACHE_MAX_LOCAL_DISK_SIZE=1120Gi` per GPU (16 GPUs × 1120Gi ≤ 18000Gi Lustre PVC).

##### 50K system prompt length (KVCache size 994 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 25.38 | 37.74 | 56.21 | 69.69 | 18607 | 354 | 18962 |
| **vLLM + CPU offloading + Lustre** | 20.12 (-21%) | 34.02 (-9.9%) | 45.83 (-18%) | 58.73 (-16%) | 22827 (+23%) | 435 (+23%) | 23262 (+23%) |

##### 70K system prompt length (KVCache size 1.3 TiB) — KV Cache >> (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 58.02 | 74.75 | 87.99 | 105.46 | 16598 | 226.65 | 16825 |
| **vLLM + CPU offloading + Lustre** | 45 (-22%) | 64.79 (-13%) | 68.28 (-22%) | 87.47 (-17%) | 21364 (+28.71%) | 291 (+28.39%) | 21656 (+28.71%) |

#### LLM-D FS Connector + Lustre

* CPU RAM allocated: `cpu_bytes_to_use=64424509440` (~64 GB per replica, ~356 GB total for 4 replicas).
* Lustre PVC = 18000 GiB.

##### 30K system prompt length (Qwen3-32B, KVCache size 653 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) | ITL (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 2.24 | 5.14 | 22.21 | 26.6 | 27148 | 836 | 27984 | 0.021 |
| **vLLM + CPU offloading + Lustre** | 1.38 (-38.4%) | 2.82 (-45.1%) | 20.45 (-7.9%) | 22.77 (-14.4%) | 28832 (+6.2%) | 828 (-1.0%) | 29661 (+6.0%) | 0.02 (-4.8%) |

##### 50K system prompt length (Llama-3.3-70B, KVCache size 994 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) | ITL (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 27.11 | 41.71 | 57.06 | 72.28 | 18333 | 350 | 18682 | 0.029 |
| **vLLM + CPU offloading + Lustre** | 15.25 (-43.7%) | 24.71 (-40.8%) | 38.55 (-32.4%) | 48.01 (-33.6%) | 27091 (+47.8%) | 517 (+47.7%) | 27609 (+47.8%) | 0.022 (-24.1%) |



### gpt-oss-120B Benchmarking Results  

The benchmark runs on 16 × H100 GPUs, distributed across 16 model servers (1 H100s per server with TP=1) using gpt-oss-120B and the same workload as in [default configuration benchmark results](#cpu-offloading-benchmarking-results). The benchmark compares to optimized baseline configuration.

For detailed results see [gpt-oss-120B benchmarking results](benchmark-results-gpt-oss-120b.md).