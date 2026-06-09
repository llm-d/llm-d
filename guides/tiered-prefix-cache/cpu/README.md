# Offloading Prefix Cache to CPU Memory

### CPU Offloading (vLLM Native)

[![Nightly - Tiered Prefix Cache E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-gke.yaml)
[![Nightly - Tiered Prefix Cache E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-ocp.yaml)

### CPU Offloading (LMCache)

[![Nightly - Tiered Prefix Cache LMCache E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-lmcache-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-cpu-offloading-lmcache-gke.yaml)

## Overview

This guide provides recipes to offload prefix cache to CPU RAM via the vLLM native offloading connector, LMCache connector and tpu-inference KVCache connector. Offloading prefix cache to CPU helps in increasing overall throughput and mitigating memory starvation on HBM for large context models and frequent multi-turn user sessions.

## Default Configuration

### GPU

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| GPUs per replica (TP)     | 4                                                       |
| GPU Accelerator           | NVIDIA H100                                             |
| CPU Cache Offload Size    | 100 GB                                                  |

### TPU

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| TPUs per replica (TP)     | 8                                                       |
| TPU Accelerator           |  TPU7x                                             |
| HBM Staging Buffer Size   | 1000 Blocks (~34 GB)                                                   |
| CPU Cache Offload Size    | 25000 Chunks (~780 GB)                                                   |

### Supported Hardware Backends

This guide supports both GPU and TPU. GPU defaults to NVIDIA H100 and TPU defaults to TPU7x. The Kustomize overlays are available in `modelserver/gpu/vllm/` and `modelserver/tpu-v7/vllm/`.

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
    export ROUTER_CHART_VERSION=v0
    export GUIDE_NAME="tiered-prefix-cache-cpu"
    export NAMESPACE=llm-d-${GUIDE_NAME}
    export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

- Create a target namespace for the installation

  ```bash
    kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar side-by-side. Default mode for standalone deployments:

```bash
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/cpu/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../prereq/gateways).
2. _Deploy the llm-d Router and an HTTPRoute_ connecting to the Gateway:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/cpu/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

---

### 2. Deploy the Model Server

Apply the Kustomize overlay setup matching your preferred offloading medium:

**For NVIDIA GPU:**

```bash
export CONNECTOR=offloading-connector # offloading-connector | lmcache-connector
export INFRA_PROVIDER=base # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/cpu/modelserver/gpu/vllm/${CONNECTOR}/${INFRA_PROVIDER}/
```

**For Google TPU v7:**

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/cpu/modelserver/tpu-v7/vllm/tpu-offloading-connector/
```

> [!NOTE]
> To enable tiered prefix caching, we customize the llm-d EPP configuration. We configure two prefix cache scorers: one for the GPU/TPU cache and another for the CPU cache.
> LRU capacity for the CPU cache must be manually configured (`lruCapacityPerServer`) because vLLM currently does not emit CPU block metrics.

---

### 3. (Optional) Enable monitoring

- Install the [Monitoring stack](../../../docs/resources/observability/setup.md).
- Deploy the monitoring resources for this guide:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```

## Verification

### 1. Get the IP of the Proxy

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

### 2. Send Test Requests

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

---

## Cleanup

To clean up the applied deployment components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/cpu/modelserver/gpu/vllm/${CONNECTOR}/${INFRA_PROVIDER}
kubectl delete namespace ${NAMESPACE}
```

---

## Benchmarking

The benchmark launches a pod (`llmdbench-harness-launcher`) that uses `inference-perf` with a shared prefix workload. This workload runs several stages with different rates. The results are saved locally to `./results/<experiment ID>`.

For more details, refer to the [benchmark instructions doc](../../../helpers/benchmark.md).

### 1. Prepare the Benchmarking Suite

Download the benchmark script:

```bash
curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
chmod u+x run_only.sh
```

Ensure a HuggingFace token secret `llm-d-hf-token` is created inside the namespace.

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/tiered-prefix-cache/cpu/benchmark-templates/guide.yaml"
```

### 3. Execute Benchmark

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
envsubst < guide.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

---

### Benchmarking Results

The current weight configuration defaults to `1:1:1:1:1` (Queue Scorer : KV Cache Utilization Scorer : GPU Prefix Cache Scorer : CPU Prefix Cache Scorer : LRU Scorer). Note that prefixes in GPU is double counted in the CPU scorer because CPU kv cache is a super set of GPU. This configuration defaults to a safe performance profile.

### GPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

The benchmark runs on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2) using Qwen3-32B.

* **Workload**: 250 prefix groups, 5 prompts per group, system prompt length of 16,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **GPU Cache Size (Total)**: 2,384,000 tokens (381 GB / 355 GiB).
* **CPU Cache Size (Total)**: 10,496,000 tokens (~1,718 GB / 1,600 GiB) at 200 GiB/replica.
* **Workload Unique Cache (Working Set)**: 4,640,000 tokens (~760 GB / 708 GiB) — comprising 4.0M system prompt tokens (250 groups × 16,000 tokens), 320,000 question tokens (1,250 prompts × 256 tokens), and 320,000 generated output tokens (1,250 prompts × 256 tokens).

**Cache capacity dynamics**:
`Total GPU HBM Cache (2.38M tokens / 355 GiB) < Workload Unique Cache (4.64M tokens / 708 GiB) < Total CPU Cache Capacity (10.49M tokens / 1,600 GiB)`

Because the total unique tokens in the workload (4.64M) exceed the GPU local cache capacity (2.38M), running on HBM cache alone (**Optimized Baseline**) causes severe cache thrashing. CPU offloading is required to store the full working set in CPU RAM. Note that the entire workload unique cache fits comfortably within the total CPU cache capacity (10.49M tokens).

Under EPP tiered routing (**GPU + CPU tier prefix aware routing**), EPP explicitly scores both GPU and CPU cache hits, ensuring optimal replica affinity routing even when GPU cache is fully thrashing.

<img src="./benchmark-results/external_prefix_cache_hits.png" width="900" alt="vLLM External Prefix Cache Hits">

Below are the benchmark results across the 5.0 to 40.0 QPS request rate stages:

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **5.0 QPS** | **Optimized Baseline (HBM-only)** | 1.62 | 2.65 | 11.98 | 21.60 | 74,638.5 |
| | **GPU + CPU tier prefix aware routing** | 1.17 (-27.8%) | 1.79 (-32.5%) | 11.49 (-4.1%) | 20.25 (-6.3%) | 82,880.0 (+11.0%) |
| **10.0 QPS** | **Optimized Baseline (HBM-only)** | 8.08 | 16.61 | 26.28 | 32.60 | 122,387.7 |
| | **GPU + CPU tier prefix aware routing** | 0.41 (-94.9%) | 1.31 (-92.1%) | 6.97 (-73.5%) | 9.23 (-71.7%) | 167,027.5 (+36.5%) |
| **15.0 QPS** | **Optimized Baseline (HBM-only)** | 23.19 | 43.64 | 41.79 | 60.08 | 121,248.1 |
| | **GPU + CPU tier prefix aware routing** | 0.38 (-98.4%) | 0.48 (-98.9%) | 7.66 (-81.7%) | 9.16 (-84.8%) | 247,732.6 (+104.3%) |
| **20.0 QPS** | **Optimized Baseline (HBM-only)** | 43.67 | 78.13 | 62.29 | 92.14 | 114,663.2 |
| | **GPU + CPU tier prefix aware routing** | 0.89 (-98.0%) | 2.66 (-96.6%) | 9.03 (-85.5%) | 11.22 (-87.8%) | 300,749.2 (+162.3%) |
| **25.0 QPS** | **Optimized Baseline (HBM-only)** | 61.01 | 110.00 | 79.91 | 128.00 | 113,870.1 |
| | **GPU + CPU tier prefix aware routing** | 4.80 (-92.1%) | 10.87 (-90.1%) | 13.24 (-83.4%) | 19.42 (-84.8%) | 318,589.9 (+179.8%) |
| **30.0 QPS** | **Optimized Baseline (HBM-only)** | 78.79 | 143.67 | 97.77 | 160.89 | 118,508.8 |
| | **GPU + CPU tier prefix aware routing** | 12.06 (-84.7%) | 23.01 (-84.0%) | 20.58 (-78.9%) | 31.07 (-80.7%) | 328,334.0 (+177.1%) |
| **35.0 QPS** | **Optimized Baseline (HBM-only)** | 97.24 | 174.85 | 116.54 | 193.17 | 116,009.0 |
| | **GPU + CPU tier prefix aware routing** | 19.28 (-80.2%) | 36.31 (-79.2%) | 27.81 (-76.1%) | 44.59 (-76.9%) | 324,511.5 (+179.7%) |
| **40.0 QPS** | **Optimized Baseline (HBM-only)** | 115.57 | 206.39 | 134.78 | 223.93 | 115,645.0 |
| | **GPU + CPU tier prefix aware routing** | 25.38 (-78.0%) | 48.14 (-76.7%) | 33.95 (-74.8%) | 56.29 (-74.9%) | 331,212.6 (+186.4%) |

### Previous Benchmarking Results

> [!NOTE]
> The following benchmark results were from a previous release and does not match the deployment of the current release. A follow up benchmark will be conducted and the results will be updated accordingly. See <https://github.com/llm-d/llm-d/issues/680>.

### GPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 9.0 | 20.9 | 37.8 | 49.7 | 38,534.8 |
| **vLLM + CPU offloading 100GB** | 6.7 (-25.6%) | 20.2 (-3.3%) | 30.9 (-18.3%) | 44.2 (-11.1%) | 46,751.0 (+21.3%) |
| **vLLM + LMCache CPU offloading 100GB** | 6.5 (-27.8%) | 18.8 (-10.0%) | 30.8 (-18.5%) | 43.0 (-13.5%) | 46,910.6 (+21.7%) |

#### Low Cache Scenario (KVCache < HBM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.12 | 0.09 | 18.4 | 19.6 | 23,389.6 |
| **vLLM + CPU offloading 100GB** | 0.13 | 0.11 | 18.6 | 20.6 | 23,032.6 |
| **vLLM + LMCache CPU offloading 100GB** | 0.15 | 0.10 | 18.9 | 19.6 | 22,772.5 |

### TPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.98 | 2.1 | 22.1 | 26.2 | 67262.3 |
| **vLLM + CPU offloading 25000 Chunks** | 0.56 (-49%) | 0.5 (-75.7%) | 20.3 (-8.1%) | 23.6 (-9.9%) | 73178.1 (+8.9%) |

#### Low Cache Scenario (KVCache < HBM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.24 | 0.23 | 16.9 | 19.9 | 25715.9 |
| **vLLM + CPU offloading 25000 Chunks** | 0.26 | 0.24 | 17.4 | 20.2 | 23,032.6 |
