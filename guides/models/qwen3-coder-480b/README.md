# Qwen3-Coder-480B on TPU 7x

This recipe is optimized for agentic code generation; see the
[agentic workloads section of the Models overview](../../../docs/well-lit-paths/models/README.md#agentic-workloads)
for the workload framing, and the [GLM-5.2](../glm-5-2/README.md) and
[NVIDIA-Nemotron-3-Ultra](../nemotron-3-ultra/README.md) guides for the other models benchmarked
against the same workload.

## Overview

This guide deploys the optimal llm-d configuration for agentic code-generation workload. The configuration includes multiple llm-d optimizations in terms of routing and KV cache management:

- **Prefix-aware routing** to optimize prefix cache reuse
- **KV cache offloading** to CPU DRAM to handle multi-turn conversations with long contexts (via KV offloading connector)
- **Load balancing** to increase cluster-wide accelerator utilization and prevent hot-spotting from bursty request patterns

## Default Configuration

| Parameter          | Value                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Model              | [Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) |
| Replicas           | 8                                                                                          |
| Accelerator        | Google TPU 7x (tpu7x)                                                                      |
| Topology           | 2x2x1                                                                                      |
| TP size / EP size  | TP=8, EP enabled                                                                           |

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| Google TPU v7x | `tpu` | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | 🟡 community | GKE only · 8 replicas × 4 chips (`tpu7x` `2x2x1`, TP=8) with CPU KV offloading · `TOPOLOGY=disaggregated` (experimental): 2 prefill + 6 decode |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

Set `TOPOLOGY` to pick the serving topology: `unified` (default) runs one pool of 8 replicas
with CPU KV offloading; `disaggregated` is the experimental Prefill/Decode configuration
(2 prefill + 6 decode, see [Prefill/Decode Disaggregated Results](#prefilldecode-disaggregated-results)).

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- A GKE cluster with a TPU v7x (`tpu7x`, `2x2x1`) node pool large enough for 8 replicas
  (4 chips each).

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d): the manifests, Helm values, and Kustomize overlays it applies live next to this guide. Set the branch and clone the repo (if you already have a checkout, skip this and run the remaining commands from inside it):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

**Set the guide-specific environment variables:**

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=qwen3-coder-480b
export NAMESPACE=llm-d-qwen3-coder-480b
export ACCELERATOR_TYPE=tpu # options: tpu
export MODEL_SERVER=vllm # options: vllm
export TOPOLOGY=unified # options: unified, disaggregated
export MODEL=Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 # the model both topologies serve
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

**Install the Gateway API Inference Extension CRDs:**

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

**Create a target namespace for the installation:**

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

## Installation Instructions

### 1. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router. The P/D disaggregated
topology uses its own router values
([`router/qwen3-coder-480b-disagg.values.yaml`](router/qwen3-coder-480b-disagg.values.yaml)):

<!-- guide:deploy.router_values start -->
```bash
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"

# only when TOPOLOGY=unified:
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"

# only when TOPOLOGY=disaggregated:
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}-disagg.values.yaml"
```
<!-- guide:deploy.router_values end -->

> [!NOTE]
> **`peakPrefillThroughput` is hardware/model-specific.** The router values set
> `peakPrefillThroughput: 16444`, calibrated for Qwen3-Coder-480B-FP8 on TPU 7x. If you
> deploy a different model or hardware, measure your own value and update
> `router/qwen3-coder-480b.values.yaml`. See the shared
> [router calibration tool](../../recipes/router/calibration/README.md).

**Deploy the router** in [Standalone Mode](../../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

**Apply the Kustomize overlay** for your topology (the unified overlay enables CPU KV offloading):

<!-- guide:deploy.modelserver start -->
```bash
# only when TOPOLOGY=unified:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/

# only when TOPOLOGY=disaggregated:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}-disaggregated/
```
<!-- guide:deploy.modelserver end -->

Wait for the deployment to become ready (with `TOPOLOGY=disaggregated`, also wait for
`deployment/agentic-serving-tpu-vllm-prefill`):

```bash
kubectl rollout status deployment/agentic-serving-tpu-vllm-decode -n ${NAMESPACE}
```

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Explain how a simple agent loop works in 3 sentences.\"}"'
```
<!-- guide:verify.tests.request end -->

**Check that the router reaches a model server that serves `MODEL`:** the response must name the model and carry generated tokens:

<!-- guide:verify.tests.served_model start -->
```bash
# The router must reach a model server that serves MODEL and returns tokens
kubectl run serve-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'R=$(curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Say hello.\", \"max_tokens\": 16}")
    echo "${R}" | grep -q "\"model\":\"${MODEL}\"" && echo "${R}" | grep -q "\"choices\"" \
      && echo "OK: ${MODEL} served through the router" \
      || { echo "FAIL: ${R}"; exit 1; }'
```
<!-- guide:verify.tests.served_model end -->

## Benchmarking

This guide comes with an `inference-perf` benchmark preset (defined in [guide.yaml](benchmark-templates/guide.yaml)) designed for agentic code-generation workloads with multi-turn interactions and tool usage. The configuration parameters include:

| Workload Characteristic | Metric / Distribution Type | Min | Max | Mean / Constant | Std Dev | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shared System Prompt** | Constant | - | - | 3,000 tokens | - | Common base instructions, libraries, and API schemas shared across all agent instances. Highly cacheable. |
| **Dynamic System Prompt** | Lognormal | 10,000 | 990,000 | 160,000 tokens | 233,600 | Repository context, file indexes, and user-specific code context. Extremely large and variable context. |
| **Turns per Conversation** | Lognormal | 1 | 3,000 | 540 turns | 48,600 | The depth of the agentic reasoning/conversational loop. Multi-turn interactions require sustaining long-lived sessions. |
| **Input Tokens per Turn** | Lognormal | 100 | 10,000 | 1,500 tokens | 1,200 | Ongoing prompt extensions (e.g., test logs, user follow-ups, modified code blocks) during conversation. |
| **Output Tokens per Turn** | Lognormal | 50 | 10,000 | 425 tokens | 825 | Model generations per turn, which are generally smaller than inputs but can spike when generating large files. |
| **Tool Call Latency** | Lognormal | 1s | 100s | 15 seconds | 55 | Time spent executing tools (compilation, unit tests, web search). Causes idle/delayed turns on the client side. |

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  ```

- Prepare HuggingFace token secret `llm-d-hf-token` in the namespace.

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/models/${GUIDE_NAME}/benchmark-templates/guide.yaml"
```

### 3. Execute Benchmark

```bash
# Benchmark parameters. CONCURRENCY_LEVEL is the number of concurrent coding sessions
# to drive; NUM_REQUESTS is fixed at 20 per session; SEED is varied per concurrency level
# so prompts don't overlap across runs (matches the published results below).
export CONCURRENCY_LEVEL=40
export NUM_REQUESTS=$((20 * CONCURRENCY_LEVEL))
export SEED=$((7 + CONCURRENCY_LEVEL))

export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')

# Render the configuration using the appropriate template:
envsubst < guide.yaml > config.yaml

./run_only.sh -c config.yaml -o ./results
```

> [!TIP]
> **Tuning Prefill-to-Decode Ratios:**
> The `vllm-disaggregated` overlay (`TOPOLOGY=disaggregated`) deploys the optimal 2:6 ratio (2 prefillers, 6 decoders). If you want to evaluate different ratios from the benchmarking report (such as 5:3 or 6:2), you can scale the active deployments directly:
>
> ```bash
> kubectl scale deployment/agentic-serving-tpu-vllm-prefill --replicas=5 -n ${NAMESPACE}
> kubectl scale deployment/agentic-serving-tpu-vllm-decode --replicas=3 -n ${NAMESPACE}
> ```

## Benchmark Results

The results below are with 8 replicas of TPU 7x (2x2x1) on the benchmark workload described above.

Scaling concurrency up to 80 sessions, the optimized configuration sustains a peak total throughput of **~120K tokens/s**, versus **~40K tokens/s** for the k8s Service baseline — roughly **3× higher**.

### Summary with 40 concurrent coding sessions

| Metric | k8s Service | llm-d-optimized | Δ Improvement |
| :--- | :--- | :--- | :--- |
| **TTFT P50 (ms)** | 17391 | 2474 | ⬇️ 85.8% |
| **Total tokens / sec** | 37424 | 93353 | ⬆️ 149.5% |
| **Input tokens / sec** | 36987 | 92705 | ⬆️ 150.6% |
| **Output tokens / sec** | 436.8 | 647.5 | ⬆️ 48.2% |

### Latency Profiles

<p float="left">
  <img src="./benchmark-results/latency_vs_throughput.png" width="32%" alt="Latency vs Throughput" />
  <img src="./benchmark-results/throughput_vs_concurrency.png" width="32%" alt="Throughput vs Concurrency" />
  <img src="./benchmark-results/ttft_vs_concurrency.png" width="32%" alt="TTFT vs Concurrency" />
</p>

### Prefill/Decode Disaggregated Results

We also evaluated the P/D disaggregated configuration on TPU 7x with different prefill-to-decode ratios under the same agentic workload framework (average prompt size **~128K tokens**, output **~1.1K tokens**).

At this scale, the KV cache size is extremely large (~20GB per request), shifting the bottleneck from prefill compute to **decoder HBM capacity**. Under a concurrency of 40, allocating more TPU nodes to the decode phase (**2:6** ratio) yields the best performance by providing sufficient aggregate HBM to avoid severe queueing and swapping:

| Metric | 2:6 (2 Prefill, 6 Decode) | 5:3 (5 Prefill, 3 Decode) | 6:2 (6 Prefill, 2 Decode) |
| :--- | :---: | :---: | :---: |
| **TTFT Median (s)** | **7.3** | 158.3 | 329.5 |
| **TTFT Mean (s)** | **31.2** | 159.2 | 317.9 |
| **TTFT P90 (s)** | **112.6** | 258.1 | 473.6 |
| **TPOT Median (ms)** | **260.2** | 1,610.0 | 1,652.5 |
| **Input Throughput (tok/s)** | **33,515.2** | 14,821.9 | 9,751.2 |
| **Output Throughput (tok/s)** | **295.1** | 128.8 | 84.2 |
| **Error Rate** | 2.8% | **2.6%** | 2.9% |

**Key Takeaways:**

1. **Decoder HBM Capacity is the Bottleneck:** With ~128K context, the KV cache size (~20GB) quickly saturates the decoder HBM. Having only 2 decoders (6:2) restricts the active request capacity, leading to severe queueing (TTFT Median of 329.5s).
2. **Optimal Ratio Shift:** Expanding decode capacity to 6 nodes (2:6) increases the aggregate HBM, reducing the TTFT Median by **45x** (7.3s) and increasing output throughput by **3.5x** (295.1 tok/s).
3. **Prefill Capacity:** Even with only 2 prefillers (2:6), they are able to sustain the required prefill throughput without becoming the primary bottleneck.
4. **Current Limitations:** Without CPU offloading optimizations, this disaggregated TPU configuration currently performs below the unified `llm-d-optimized` baseline. Integrating these optimizations into TPU disaggregated deployments is an active area of development.

**Note**: As of June 2026 we are actively working on improving the following for TPU deployments:

- Long context performance
- P/D disaggregation
- KV Cache offloading support

This guide and performance numbers will be updated as further optimizations become available.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
# only when TOPOLOGY=unified:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/

# only when TOPOLOGY=disaggregated:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}-disaggregated/
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
