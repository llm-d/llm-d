# Offloading Prefix Cache to CPU Memory

## Overview

This guide provides recipes to offload prefix cache to CPU RAM via the vLLM native offloading connector and the LMCache connector.

## Prerequisites

* All prerequisites from the [upper level](../README.md).
* Have the [proper client tools installed on your local system](../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../prereq/infrastructure/README.md).
* Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-pfc-cpu # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../prereq/client-setup/README.md#huggingface-token) to pull models.
* [Choose an llm-d version](../../prereq/client-setup/README.md#llm-d-version)

## Installation

```bash
cd guides/tiered-prefix-cache/cpu
```

### Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../recipes/gateway/README.md).

### Deploy vLLM Model Server

<!-- TABS:START -->

<!-- TAB:Offloading Connector:default -->
#### Offloading Connector

Deploy the vLLM model server with the `OffloadingConnector` enabled.

```bash
kubectl apply -k ./manifests/vllm/offloading-connector -n ${NAMESPACE}
```

<!-- TAB:LMCache Connector -->
#### LMCache Connector

Deploy the vLLM model server with the `LMCache` connector enabled.

```bash
kubectl apply -k ./manifests/vllm/lmcache-connector -n ${NAMESPACE}
```

<!-- TABS:END -->

### Deploy InferencePool

To deploy the `InferencePool`, select your provider below.

<!-- TABS:START -->

<!-- TAB:GKE:default -->

#### GKE

This command deploys the `InferencePool` on GKE with GKE-specific monitoring enabled.

```bash
helm install llm-d-infpool \
    -n ${NAMESPACE} \
    -f ./manifests/inferencepool/values.yaml \
    --set "provider.name=gke" \
    --set "inferenceExtension.monitoring.prometheus.enabled=true" \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
    --version v1.3.0
```

<!-- TAB:Istio -->

#### Istio

This command deploys the `InferencePool` with Istio, enabling Prometheus monitoring.

```bash
helm install llm-d-infpool \
    -n ${NAMESPACE} \
    -f ./manifests/inferencepool/values.yaml \
    --set "provider.name=istio" \
    --set "inferenceExtension.monitoring.prometheus.enabled=true" \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
    --version v1.3.0
```

<!-- TAB:Kgateway -->

#### Kgateway

This command deploys the `InferencePool` with Kgateway.

```bash
helm install llm-d-infpool \
    -n ${NAMESPACE} \
    -f ./manifests/inferencepool/values.yaml \
    --set "provider.name=kgateway" \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
    --version v1.3.0
```

<!-- TABS:END -->

To enable tiered prefix caching, we customize the `InferencePool` configuration (see [`manifests/inferencepool/values.yaml`](./manifests/inferencepool/values.yaml)). We configure two prefix cache scorers: one for the GPU cache and another for the CPU cache.

For the CPU cache, we must manually configure the `lruCapacityPerServer` because vLLM currently does not emit CPU block metrics.

The current weight configuration is `2:2:1:1` (Queue Scorer : KV Cache Utilization Scorer : GPU Prefix Cache Scorer : CPU Prefix Cache Scorer). The current CPU offloading copies GPU cache entries to CPU, essentially making CPU cache a super set of GPU. This weight configuration ensures that the combined weight of the GPU and CPU prefix cache scorers equals 2.

You can tune these values, particularly the ratio between the GPU and CPU scorers, to suit your specific requirements. The current configuration has demonstrated improved performance in our [Benchmark](#benchmark) tests.

## Verifying the installation

You can verify the installation by checking the status of the created resources.

### Check the Gateway

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`.

```text
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         16m
```

### Check the HTTPRoute

```bash
kubectl get httproute -n ${NAMESPACE}
```

```text
NAME          HOSTNAMES   AGE
llm-d-route               17m
```

### Check the InferencePool

```bash
kubectl get inferencepool -n ${NAMESPACE}
```

```text
NAME            AGE
llm-d-infpool   16m
```

### Check the Pods

```bash
kubectl get pods -n ${NAMESPACE}
```

You should see the InferencePool's endpoint pod and the model server pods in a `Running` state.

```text
NAME                                  READY   STATUS    RESTARTS   AGE
llm-d-infpool-epp-xxxxxxxx-xxxxx     1/1     Running   0          16m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
```

## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}

kubectl delete -f ./manifests/pvc.yaml -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/offloading-connector -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/<offloading-connector|lmcache-connector> -n ${NAMESPACE}
kubectl delete -k ../recipes/gateway/<gke-l7-regional-external-managed|istio|kgateway|kgateway-openshift> -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Appendix

### Benchmark

The following benchmark results demonstrate the performance improvements of using vLLM's native CPU offloading and LMCache CPU offloading.

#### Benchmark Setup

* **Hardware:**
  * A total of 16 H100 GPUs, each with 80GB of HBM, were used.
  * The GPUs were distributed across 4 `a3-highgpu-4g` instances, with 4 GPUs per instance.

* **vLLM Configuration:**
  * `gpu_memory_utilization` was set to `0.65` to reduce the pressure on the benchmark tool. In
    production configuration this is typically set to a higher value such as 0.9.
  * CPU offloading was enabled with `num_cpu_blocks` set to `41000`, which provides approximately 100GB of CPU cache.
* **LMCache Configuration:**
  * For LMCache setup, `LMCACHE_MAX_LOCAL_CPU_SIZE` is set to 100 GB.

The benchmark was conducted using the [inference-perf](https://github.com/kubernetes-sigs/inference-perf) tool with the following hardware, memory, and workload configurations:

* **Workload:**
  * The two different workloads were tested with a constant concurrency of 45 requests.
  * **High Cache:**
    * `num_groups`: 45
    * `system_prompt_len`: 30,000
    * `question_len`: 256
    * `output_len`: 1024
    * `num_prompts_per_group`: 10
  * **Low Cache:**
    * `num_groups`: 45
    * `system_prompt_len`: 8000
    * `question_len`: 256
    * `output_len`: 1024
    * `num_prompts_per_group`: 10

* **Memory Calculation:**
  * The KVCache size for the `Qwen/Qwen3-32B` model is approximately 0.0002 GB per token.
  * With `gpu_memory_utilization` at 0.65, there are 9271 GPU blocks available per engine.
  * The available HBM for KVCache per engine is approximately 24.3GB (9271 blocks * 2.62 MB/block).
  * The total available HBM for the KVCache across the entire system was 193.4 GB (8 engines * 24.3 GB/engine).

#### Key Findings

* In **High cache scenarios**, where the KVCache size exceeds the available HBM, both the vLLM native CPU offloading connector and LMCache connector significantly enhance performance.
* In **Low cache scenarios**, where the KVCache fits entirely within the GPU's HBM, all offloading configurations perform similarly to the baseline. However, consistent slight decreases in performance across metrics indicate a small overhead associated with enabling CPU offloading, even when it is not actively utilized.

#### High Cache Performance

The following table compares the performance of the baseline vLLM with the vLLM using the CPU offloading connector when the KVCache size is larger than the available HBM.

| HBM < KVCache < HBM + CPU RAM | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 9.0 | 20.9 | 37.8 | 49.7 | 38534.8 |
| **vLLM + CPU offloading 100GB** | 6.7 (-25.6%) | 20.2 (-3.3%) | 30.9 (-18.3%) | 44.2 (-11.1%) | 46751.0 (+21.3%) |
| **vLLM + LMCache CPU offloading 100GB** | 6.5 (-27.8%) | 18.8 (-10.0%) | 30.8 (-18.5%) | 43.0 (-13.5%) | 46910.6 (+21.7%) |

#### Low Cache Performance

The following table shows that when the KVCache fits within the HBM, the performance of all configurations is similar, indicating minimal but measurable overhead from the CPU offloading mechanism.

| KVCache < HBM | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.12 | 0.09 | 18.4 | 19.6 | 23389.6 |
| **vLLM + CPU offloading 100GB** | 0.13 | 0.11 | 18.6 | 20.6 | 23032.6 |
| **vLLM + LMCache CPU offloading 100GB** | 0.15 | 0.10 | 18.9 | 19.6 | 22772.5 |
