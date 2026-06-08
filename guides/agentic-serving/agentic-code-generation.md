# Agentic Code Generation Guide

## Overview

This guide deploys the optimal llm-d configuration for agentic workloads using agentic code generation as the example workload. The configuration includes multiple llm-d optimizations in terms of routing and KV cache management:
- **Prefix-aware routing** to optimize prefix cache reuse (via `prefix-cache-scorer`)
- **KV cache offloading** to CPU DRAM to handle multi-turn conversations with long contexts (via `kv-cache-utilization-scorer` and KV offloading connector)
- **Concurrency balancing** to prevent replica hotspotting from bursty request patterns (via `queue-scorer`)

## Workload Profile & Key Configurations

To understand why the `llm-d` configuration for agentic workloads is set up this way, it is helpful to examine the typical workload profile of an agentic code generation task. The benchmarking suite uses the following configuration parameters (defined in [guide.yaml](benchmark-templates/guide.yaml)) representing a realistic distribution of agentic interactions:

### Workload Distribution Profile

| Workload Characteristic | Metric / Distribution Type | Min | Max | Mean / Constant | Std Dev | Description & Importance |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shared System Prompt** | Constant | - | - | 3,000 tokens | - | Common base instructions, libraries, and API schemas shared across all agent instances. Highly cacheable. |
| **Dynamic System Prompt** | Lognormal | 10,000 | 990,000 | 160,000 tokens | 233,600 | Repository context, file indexes, and user-specific code context. Extremely large and variable context. |
| **Turns per Conversation** | Lognormal | 1 | 3,000 | 540 turns | 48,600 | The depth of the agentic reasoning/conversational loop. Multi-turn interactions require sustaining long-lived sessions. |
| **Input Tokens per Turn** | Lognormal | 100 | 10,000 | 1,500 tokens | 1,200 | Ongoing prompt extensions (e.g., test logs, user follow-ups, modified code blocks) during conversation. |
| **Output Tokens per Turn** | Lognormal | 50 | 10,000 | 425 tokens | 825 | Model generations per turn, which are generally smaller than inputs but can spike when generating large files. |
| **Tool Call Latency** | Lognormal | 1s | 100s | 15 seconds | 55 | Time spent executing tools (compilation, unit tests, web search). Causes idle/delayed turns on the client side. |

---

### Deep Dive: Why the llm-d Routing and KV Cache Configurations are Critical

Given the characteristics of the agentic code generation workload shown above, default serving configurations would suffer from severe performance degradation. Here is why `llm-d`'s specific optimizations are vital:

#### 1. Prefix-Aware Routing (`prefix-cache-scorer`)
* **The Challenge:** Dynamic system prompts or code context can be extremely large (up to 160K tokens). Processing a 160K token prompt (prefill phase) from scratch on every turn or new conversation is computationally expensive and introduces massive time-to-first-token (TTFT) latency.
* **The Optimization:** The `prefix-cache-scorer` is assigned the highest weight (`weight: 3`). It routes requests to replica pods that already have the matching prefix (system prompt and early turns) cached in their KV cache.
* **The Impact:** Eliminates redundant prefill computation. Consecutive turns of the same agentic conversation or separate conversations sharing the same repository context achieve near-instantaneous TTFT.

#### 2. KV Cache Offloading to CPU DRAM (`KVOffloadConnector`)
* **The Challenge:** Maintaining active KV caches for dozens of long-running conversations (up to 256,000 tokens per model instance and up to 3,000 turns) exceeds the fast but limited HBM (High Bandwidth Memory) on TPU/GPU accelerators.
* **The Optimization:** The `KVOffloadConnector` offloads inactive KV cache blocks from TPU/GPU HBM to the host CPU DRAM, monitored dynamically via the `kv-cache-utilization-scorer` (`weight: 2`).
* **The Impact:** Replicas can handle significantly larger context windows (160K to 1M) and higher concurrency without running out of memory (OOM) or suffering from aggressive context eviction, while restoring cache blocks rapidly on subsequent turns.

#### 3. Concurrency Balancing with Queue-Based Routing (`queue-scorer`)
* **The Challenge:** The multi-turn interactions and unpredictable tool call delays make the arrival pattern of new requests highly bursty and asynchronous. Because agentic queries require extremely heavy prefill and decode computation (due to very large context sizes), routing requests solely based on prefix matching can lead to severe **hotspotting**, where a single replica's request queue builds up dramatically while other replicas remain underutilized.
* **The Optimization:** The `queue-scorer` (`weight: 2`) tracks the number of active and queued requests on each backend replica.
* **The Impact:** Acts as a concurrency balancer and guardrail. If a sticky replica becomes overloaded with pending work, the `queue-scorer` shifts new incoming requests to less busy replicas. This prevents excessive queuing latency and balances the processing load evenly across the cluster.


## Default Configuration

| Parameter          | Value                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Model              | [Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) |
| Replicas           | 8                                                                                          |
| Accelerator        | Google TPU v7 (tpu7x)                                                                      |
| Topology           | 2x2x1                                                                                      |
| TP size / EP size  | TP=8, EP enabled                                                                           |

### Supported Hardware Backends

| Backend             | Directory                      | Notes                       |
| ------------------- | ------------------------------ | --------------------------- |
| Google TPU (vLLM)   | `modelserver/tpu/vllm/`        | TPU v7 / tpu7x 2x2x1 (nightly) |

## Prerequisites

- Installed proper client tools (kubectl, helm).
- Set the following environment variables:
  ```bash
  export GAIE_VERSION=v1.5.0
  export GUIDE_NAME="agentic-workloads"
  export NAMESPACE=llm-d-agentic-workloads
  ```
- Create the namespace:
  ```bash
  kubectl create namespace ${NAMESPACE}
  ```

## Installation Instructions

### 1. Deploy the llm-d Router

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/recipes/router/base.values.yaml \
    -f guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

### 2. Deploy the Model Server (TPUs)

Apply the Kustomize overlays for TPU:

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/tpu/vllm/
```

## Verification

### 1. Get the IP of the Proxy

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send Test Requests

Open a temporary interactive shell inside the cluster:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Send a completion request:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        "prompt": "Explain how a simple agent loop works in 3 sentences."
    }' | jq
```

## Benchmarking

This guide comes with an `inference-perf` benchmark preset designed for conversation replay workloads mimicking agent multi-turn interactions and tool usage.

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  ```

- Prepare HuggingFace token secret `llm-d-hf-token` in the namespace.

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/${GUIDE_NAME}/benchmark-templates/guide.yaml"
```

### 3. Execute Benchmark

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
envsubst < guide.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Cleanup

To clean up resources:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/tpu/vllm/
kubectl delete namespace ${NAMESPACE}
```
