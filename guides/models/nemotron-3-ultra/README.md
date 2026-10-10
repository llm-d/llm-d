# NVIDIA-Nemotron-3-Ultra-550B on H200

This recipe is optimized for agentic code generation; see the
[agentic workloads section of the Models overview](../../../docs/well-lit-paths/models/README.md#agentic-workloads)
for the workload framing, and the [GLM-5.2](../glm-5-2/README.md) and
[Qwen3-Coder-480B](../qwen3-coder-480b/README.md) guides for the other models benchmarked against
the same workload.

## Overview

This guide deploys [RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block](https://huggingface.co/RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block)
on 8 H200 nodes, **prefill/decode disaggregated** into 6 prefill and 2 decode replicas to absorb
the large ISL:OSL ratio of multi-turn agentic sessions (heavy prefill, lighter decode). The
configuration layers the agentic optimizations onto disaggregated serving:

- **P/D disaggregation** so heavy prefill never stalls decode, stabilizing ITL.
- **Disagg-aware, prefix-cache routing** that scores both the on-device (GPU) and CPU-offload
  prefix caches when picking a prefill/decode endpoint.
- **KV cache offloading** to CPU DRAM — `200 GiB` per model server (`~1.6 TB` across the 8
  replicas) — to extend the cacheable working set far beyond HBM for long, resumable sessions.
- **FP8 block weights + FP8 KV cache** to fit the `~563 GB` of weights at `TP=8` and leave KV
  headroom; the MoE is served with Expert Parallelism.
- **`VLLM_PREFIX_CACHE_RETENTION_INTERVAL=0`** to raise multi-turn prefix-cache hit rates. This is
  enabled here because `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` is a
  [Mamba-hybrid model](https://huggingface.co/RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block/blob/main/config.json)
  (`NemotronHForCausalLM`); vLLM rejects the flag on full-attention models, so it is set
  per-deployment rather than in the baseline manifests.

> 🚧 This deployment uses development images (the endpoint-picker and the disaggregation routing
> sidecar) for the P/D-disaggregation scheduling plugins. Pin them to a release before relying on
> this in production.

## Default Configuration

| Parameter          | Value                                                                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| Model              | [RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block](https://huggingface.co/RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block) |
| Accelerator        | NVIDIA H200 (8 nodes, 8 GPUs each)                                                                                                  |
| Serving topology   | P/D disaggregated — 6 prefill replicas, 2 decode replicas                                                                           |
| TP size / EP size  | TP=8, EP enabled                                                                                                                    |
| KV cache           | FP8-quantized, with `~1.6 TB` CPU offload (`200 GiB`/replica)                                                                       |

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA H200 | `gpu` | `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` | 🟡 community | 8 nodes × 8 H200 · P/D disaggregated, 6 prefill + 2 decode replicas × TP=8 · `INFRA_PROVIDER`: `base` (HuggingFace download), `gke` (model from a GCS bucket) |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (`INFRA_PROVIDER=gke` only) Copy the model to a Google Cloud Storage bucket the cluster can
  mount, as described in [Downloading the Model to Your GCS Bucket](modelserver/gpu/vllm/gke/README.md).

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
export GUIDE_NAME=nemotron-3-ultra
export NAMESPACE=llm-d-nemotron-3-ultra
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=base # options: base, gke
export MODEL=RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block # the model both overlays serve
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

**Prepare the paths to the `helm` values files** for the `llm-d` router. This deployment uses the
disaggregation-aware router values
([`router/nemotron-3-ultra.values.yaml`](router/nemotron-3-ultra.values.yaml)), which run
separate `prefill` and `decode` scheduling profiles:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

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

> [!NOTE]
> This guide provides two ways to deploy the model server. The default one (`INFRA_PROVIDER` = `base`) is to download the `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` model from HuggingFace every time.
> However, the model is ~560GB in size. Downloading a model of this scale directly from HuggingFace can take over an hour, and because every deployment triggers a new download, it creates a significant bottleneck.
> To save time and accelerate the deployment process, we highly recommend saving the model to a Google Cloud Storage (GCS) bucket and accessing it directly from there. Please check the [Readme](modelserver/gpu/vllm/gke/README.md) for this suggested deployment.

**Apply the Kustomize overlay** for the Nemotron-3-Ultra H200 deployment:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

This deploys the 6 prefill and 2 decode replicas. Wait for them to become ready (model load is
large; the startup probe allows up to an hour):

```bash
kubectl rollout status deployment/agentic-serving-gpu-vllm-prefill -n ${NAMESPACE}
kubectl rollout status deployment/agentic-serving-gpu-vllm-decode -n ${NAMESPACE}
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

## Driving It with a Coding Agent

This deployment ships ready-to-use client configs for two coding agents, both pre-pointed at the
served model. First, port-forward the router's OpenAI-compatible endpoint to `localhost:8000`
(the EPP service exposes it on port `80`):

```bash
kubectl port-forward -n ${NAMESPACE} service/${GUIDE_NAME}-epp 8000:80
```

**[Claude Code](https://claude.com/product/claude-code)** — source the environment file
([`claude.env`](modelserver/gpu/vllm/claude.env)) and launch:

```bash
source ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/gpu/vllm/claude.env && claude
```

**[opencode](https://opencode.ai/docs/)** — point `OPENCODE_CONFIG` at the provided config
([`opencode.json`](modelserver/gpu/vllm/opencode.json)) and launch:

```bash
OPENCODE_CONFIG="${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/gpu/vllm/opencode.json" opencode
```

## Benchmarking

This guide comes with an `inference-perf` benchmark preset (defined in [guide.yaml](benchmark-templates/guide.yaml)) designed for agentic code-generation workloads with multi-turn interactions and tool usage. The configuration parameters include:

| Workload Characteristic | Metric / Distribution Type | Min | Max | Mean / Constant | Std Dev | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shared System Prompt** | Constant | - | - | 3,000 tokens | - | Common base instructions, libraries, and API schemas shared across all agent instances. Highly cacheable. |
| **Dynamic System Prompt** | Lognormal | 10,000 | 990,000 | 160,000 tokens | 233,600 | Repository context, file indexes, and user-specific code context. Large and variable context. |
| **Turns per Conversation** | Lognormal | 1 |3,000 | 540 turns | 48,600 | The depth of the agentic reasoning/conversational loop. Multi-turn interactivetions require sustaining long-lived sessions. |
| **Input Tokens per Turn** | Lognormal | 100 | 10,000 | 1,500 tokens | 1,200 | Ongoing prompt extensions (e.g., test logs, user follow-ups, modified code blocks) during conversation. |
| **Output Tokens per Turn** | Lognormal | 50 | 10,000 | 425 tokens | 825 | Model generations per turn, which are generally smaller than inputs but can spike when generating large files. |

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

The request rate and workload shape are fixed in the template, so only the endpoint needs to be
resolved before rendering:

```bash
# Benchmark parameters. CONCURRENCY_LEVEL is the number of concurrent coding sessions
# to drive; NUM_REQUESTS is fixed at 20 per session; SEED is varied per concurrency level
# so prompts don't overlap across runs (matches the published results below).
export CONCURRENCY_LEVEL=40
export NUM_REQUESTS=$((20 * CONCURRENCY_LEVEL))
export SEED=$((7 + CONCURRENCY_LEVEL))

export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
envsubst < guide.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Benchmark Results

The results below are with 8 replicas of H200 GPU on the benchmark workload described above. Scaling concurrency up to 80 sessions.

### Summary with 60 concurrent coding sessions

| Metric                  | k8s Service | llm-d-optimized  | Δ Improvement |
| :---                    | :---        | :---             | :---          |
| **TTFT P50 (ms)**       | 49462       | 20543            | ⬇️  58%        |
| **Total tokens / sec**  | 64958       | 67645            | ⬆️  4%         |
| **Input tokens / sec**  | 64474       | 66716            | ⬆️  3%         |
| **Output tokens / sec** |   484       |   929            | ⬆️  92%        |

### Latency Profiles

<p float="left">
  <img src="./benchmark-results/latency_vs_throughput.png" width="33%" alt="Latency vs Throughput" />
  <img src="./benchmark-results/throughput_vs_concurrency.png" width="33%" alt="Throughput vs Concurrency" />
  <img src="./benchmark-results/ttft_vs_concurrency.png" width="33%" alt="TTFT vs Concurrency" />
</p>

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
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
