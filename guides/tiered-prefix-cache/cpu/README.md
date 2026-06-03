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

### Alternative GPU configuration

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) |
| GPUs per replica (TP)     | 1                                                       |
| GPU Accelerator           | NVIDIA H100                                             |
| CPU Cache Offload Size    | 100 GB                                                  |


### TPU

| Parameter                 | Value                                                   |
| ------------------------- | ------------------------------------------------------- |
| Model                     | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| TPUs per replica (TP)     | 8                                                       |
| TPU Accelerator           |  TPU7x                                                  |
| HBM Staging Buffer Size   | 1000 Blocks (~34 GB)                                    |
| CPU Cache Offload Size    | 25000 Chunks (~780 GB)                                  |



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
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```

- Create a target namespace for the installation

  ```bash
    kubectl create namespace ${NAMESPACE}
  ```

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar side-by-side. Default mode for standalone deployments:

```bash
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f guides/recipes/router/base.values.yaml \
    -f guides/tiered-prefix-cache/cpu/router/${GUIDE_NAME}.values.yaml \
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
    -f guides/recipes/router/base.values.yaml \
    -f guides/tiered-prefix-cache/cpu/router/${GUIDE_NAME}.values.yaml \
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
kubectl apply -n ${NAMESPACE} -k guides/tiered-prefix-cache/cpu/modelserver/gpu/vllm/${CONNECTOR}/${INFRA_PROVIDER}/
```

**For Google TPU v7:**

```bash
kubectl apply -n ${NAMESPACE} -k guides/tiered-prefix-cache/cpu/modelserver/tpu-v7/vllm/tpu-offloading-connector/
```

> [!NOTE]
> To enable tiered prefix caching, we customize the llm-d EPP configuration. We configure two prefix cache scorers: one for the GPU/TPU cache and another for the CPU cache.
> LRU capacity for the CPU cache must be manually configured (`lruCapacityPerServer`) because vLLM currently does not emit CPU block metrics.

---

### 3. (Optional) Enable monitoring

- Install the [Monitoring stack](../../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide:

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
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
kubectl delete -n ${NAMESPACE} -k guides/tiered-prefix-cache/cpu/modelserver/gpu/vllm/${CONNECTOR}/${INFRA_PROVIDER}
kubectl delete namespace ${NAMESPACE}
```

---

## Benchmarking

For instructions on setting up standard workloads and running performance analyses against this guide, refer to the [benchmark instructions doc](../../../helpers/benchmark.md).

The current weight configuration defaults to `2:2:3:2` (Queue Scorer : KV Cache Utilization Scorer : GPU/TPU Prefix Cache Scorer : CPU Prefix Cache Scorer). This configuration defaults to a safe performance profile.

The benchmark compared optimized baseline to optimized baseline+kv cache offload to cpu. It was run on `gpt-oss-120b` (https://huggingface.co/openai/gpt-oss-120b) with 16 replicas x 1 NVIDIA GPU (tensor-parallel-size=1) and the following additional parameters:

- '--gpu-memory-utilization=0.95'
- '--enable-auto-tool-choice'
- '--tool-call-parser=openai'
- '--reasoning-parser=openai_gptoss'
- '--block-size=128'

The syntehtic workload used shared prefix data with high load at poisson rate of 500 req/s, for the duration of 25 seconds. The benchmarking was executed using [llm-d skills](https://github.com/llm-d-incubation/llm-d-skills).

### GPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)


### Request Outcomes

| Metric | **Baseline llm-d** | Run B |
| --- | --- | --- |
| Total requests sent | 12,500 | 12,500 |
| **Successful** | **6,614 (52.9%)** | **9,311 (74.5%)** |
| Failed/Timed out | 5,886 (47.1%) | 3,189 (25.5%) |

### Throughput

| Metric | **Baseline llm-d** | **llm-d + CPU offloading 100GB** | Delta |
| --- | --- | --- | --- |
| Requests/sec (successful) | 20.35 | 28.64 | **+8.3 (+40.7%)** |
| Output tokens/sec | 20,207 | 29,726 | **+9518.8 (+47.1%)** |
| Total tokens/sec | 171,119 | 242,247 | **+71128.3 (+41.6%)** |
| Input tokens/sec | 150,911 | 212,521 | **+61609.5 (+40.8%)** |

### Latency (successful requests only)

| Metric | Run A | Run B | B vs A |
| --- | --- | --- | --- |
| Mean request latency | 144.3s | 146.9s | +2.5 (+1.7%) |
| Median request latency | 137.2s | 135.5s | -1.7 (-1.2%) |
| P90 request latency | 268.1s | 264.3s | -3.7 (-1.4%) |
| Mean TTFT | 93.3s | 87.0s | -6.3 (-6.8%) |
| Median TTFT | 3.9s | 6.0s | **+2.2 (+56.0%)** |
| P90 TTFT | 247.5s | 236.6s | -10.9 (-4.4%) |
| **Mean TPOT** | 51.1ms | 59.9ms | **+8.8 (+17.2%)** |
| Median TPOT | 29.5ms | 48.3ms | **+18.7 (+63.4%)** |
| P90 TPOT | 123.5ms | 130.6ms | +7.1 (+5.8%) |
| ITL mean | 51.1ms | 59.9ms | **+8.8 (+17.2%)** |

### vLLM Server Metrics (fleet aggregate)

| Metric | **Baseline llm-d**  | **llm-d + CPU offloading 100GB** |
| --- | --- | --- |
| **Internal GPU cache hit rate** | **7.2%** | **4.1%** |
| Internal cache hits (tokens) | 130.1M | 49.0M |
| Internal cache queries (tokens) | 1815.0M | 1207.9M |
| **External (CPU offload) hit rate** | N/A | **93.4%** |
| External cache hits (tokens) | N/A | 39.8M |
| External cache queries (tokens) | N/A | 42.6M |


<!--| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
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
-->