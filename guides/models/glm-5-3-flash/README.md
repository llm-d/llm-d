# GLM-5.3-Flash on B200

## Overview

This guide deploys [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) (`Glm5NextForConditionalGeneration`, 320B total / 18B active parameters, 288 routed experts) using vLLM's **Prefill/Decode (P/D) disaggregation** with NIXL (`NixlConnector`) in a wide expert parallel (`Wide-EP`) topology managed by a single `DisaggregatedSet`. It composes the [wide expert parallelism](../../wide-ep/README.md) and [P/D disaggregation](../../pd-disaggregation/README.md) foundations.

* **Prefill role (`size: 2` — 2 pods / 16 GPUs):** Runs `DP=16, EP=16, TP=1` across 2 × 8-GPU nodes (18 routed experts per GPU) using vLLM's DP Supervisor (`--data-parallel-multi-port-external-lb`, rank ports `8000`–`8007`, supervisor port `8008`).
* **Decode role (`size: 2` — 2 pods / 16 GPUs):** Runs `DP=16, EP=16, TP=1` across 2 × 8-GPU nodes (18 routed experts per GPU) with the `llm-d-routing-sidecar` (`routing-proxy` on ports `8000`–`8007` forwarding to vLLM decode worker ports `8200`–`8207`, supervisor port `8208`).
* **Hybrid KDA + Sparse NoPE MLA KV Transfer (`NixlConnector`):** `GLM-5.3-Flash` interleaves Kimi Delta Attention (`KDA` linear attention backed by `MambaSpec` conv + recurrent state) with sparse NoPE Multi-head Latent Attention (`MLA`). Both prefill and decode set `VLLM_SSM_CONV_STATE_LAYOUT=DS` so NIXL's 3-read Mamba/KDA conv state transfer decomposes and transfers the KDA conv/recurrent states alongside the FP8 MLA KV cache over RDMA.

**Status:** community model guide. Benchmarked on GKE with 4 × `a4-highgpu-8g` (32 × NVIDIA B200) and RoCE RDMA through GPU DRA and DRANET, and also run on 4 × `a3-ultragpu-8g` (32 × NVIDIA H200).

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) |
| Architecture | `Glm5NextForConditionalGeneration` (Hybrid KDA Linear Attention + Sparse NoPE MLA, 288 routed experts) |
| Workload Topology | P/D Disaggregated (`DisaggregatedSet`: 2 Prefill pods + 2 Decode pods) |
| Prefill Parallelism | `DP=16, EP=16, TP=1` (2 × 8-GPU nodes = 16 GPUs, 18 routed experts/GPU) |
| Decode Parallelism | `DP=16, EP=16, TP=1` (2 × 8-GPU nodes = 16 GPUs, 18 routed experts/GPU) |
| Total GPUs | **32** (4 × 8-GPU nodes, e.g., GKE `a4-highgpu-8g` with 32 × NVIDIA B200 GPUs) |
| DP Mode | vLLM DP Supervisor (`--data-parallel-multi-port-external-lb`) |
| KV Cache Dtype | `fp8` (`--kv-cache-dtype fp8`) |
| KV Transfer | `NixlConnector` (`VLLM_SSM_CONV_STATE_LAYOUT=DS`) |
| All-to-All Backend | `allgather_reducescatter` (see [All-to-All Backend](modelserver/gpu/vllm/gke/README.md#all-to-all-backend) in the GKE overlay README) |
| MoE Backend | `deep_gemm` |
| Max Model Length | `131072` (`MAX_MODEL_LEN`) |
| Reasoning / Tool Parsers | `glm47` / `glm47` (`--enable-auto-tool-choice`) |

### Supported Hardware Backends

| Backend | Directory | Notes |
| --- | --- | --- |
| NVIDIA GPU (Base) | `modelserver/gpu/vllm/base/` | Base `DisaggregatedSet` (2 prefill + 2 decode pods) |
| NVIDIA GPU (GKE) | `modelserver/gpu/vllm/gke/` | GKE A4 (`a4-highgpu-8g`, B200) / A3 Ultra (`a3-ultragpu-8g`, H200) with GPU DRA and DRANET |

## Prerequisites

* Complete the [wide expert parallelism prerequisites](../../wide-ep/README.md#prerequisites):
  * A Kubernetes cluster with **4 × 8-GPU RDMA-capable nodes** (32 GPUs total, e.g., 4 × GKE `a4-highgpu-8g` nodes with NVIDIA B200 GPUs, GPU DRA, and GKE managed DRANET).
  * On GKE, label the GPU nodes with `cloud.google.com/gke-networking-dra-driver=true` so `gke-managed-networking-dra-driver` publishes `dra.net` `ResourceSlices` for all 4 nodes:
    ```bash
    kubectl label nodes -l cloud.google.com/gke-accelerator=nvidia-b200 \
      cloud.google.com/gke-networking-dra-driver=true --overwrite
    kubectl get resourceslices
    ```

    See [GPU DRA and DRANET on GKE](../../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke) for the cluster, node pool, and driver setup.
  * LeaderWorkerSet controller `v0.11.1+` installed with `DisaggregatedSet` enabled (`--set enableDisaggregatedSet=true`).
  * Gateway API Inference Extension CRDs installed.
* Set the environment variables:
  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="glm-5-3-flash"
  export NAMESPACE="llm-d-glm-5-3-flash"
  export MODEL="zai-org/GLM-5.3-Flash"
  ```
* Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.
* Create the namespace and the `llm-d-hf-token` secret with the key [`HF_TOKEN`](../../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:
  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```

## Installation

### 1. Deploy the `llm-d` Router (Disaggregated P/D Mode)

#### Standalone Mode

```bash
helm upgrade --install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm upgrade --install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Disaggregated `GLM-5.3-Flash` Model Server (2 Prefill + 2 Decode Pods)

```bash
export INFRA_PROVIDER=gke # options: base, gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

Wait for both `prefill` (`0`, `0-1`) and `decode` (`0`, `0-1`) pods to reach `Ready`:

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=GLM-5.3-Flash -w
```

Expected pods (2 prefill + 2 decode across 4 × 8-GPU nodes):

```text
NAME                                               READY   STATUS    RESTARTS
pod/wide-ep-vllm-glm-5-3-flash-0-<rev>-decode-0         2/2     Running   0
pod/wide-ep-vllm-glm-5-3-flash-0-<rev>-decode-0-1       2/2     Running   0
pod/wide-ep-vllm-glm-5-3-flash-0-<rev>-prefill-0        1/1     Running   0
pod/wide-ep-vllm-glm-5-3-flash-0-<rev>-prefill-0-1      1/1     Running   0
```

## Verification

### 1. Get the Router / Gateway IP

#### Standalone Mode

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

```bash
curl -s http://${IP}/v1/models | jq
```

```bash
curl -s -X POST http://${IP}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d "{
        \"model\": \"${MODEL}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Explain Prefill/Decode disaggregation in two sentences.\"}],
        \"max_tokens\": 256
    }" | jq
```

## Benchmarking

### 1. Throughput Benchmark (2K ISL / 2K OSL)

This guide includes [`inference-perf.yaml`](inference-perf.yaml), configured for [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with `concurrency_level=2048`, `num_requests=8192`, and `2000 ISL / 2000 OSL` random tokens against `zai-org/GLM-5.3-Flash`.

> [!IMPORTANT]
> At `concurrency_level=2048` with 2,000-token prompts, ensure the `glm-5-3-flash-epp` router pod is scheduled on a node with sufficient CPU (e.g., `16` vCPUs / `16Gi`–`32Gi` memory) so prefix-cache hashing does not saturate smaller CPU nodes.

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/inference-perf.yaml
```

Follow the job's logs until it completes:

```bash
kubectl logs -n ${NAMESPACE} job/inference-perf -f
```

### 2. Multi-Turn Agentic Prefix-Caching Benchmark (`conversation_replay` up to 100K Input Tokens)

[`inference-perf-agentic.yaml`](inference-perf-agentic.yaml) uses `inference-perf`'s `conversation_replay` generator (`streaming: true`, `max_model_len: 100000`) to simulate **32 concurrent multi-turn coding sessions** (`320` total requests). Each session has a `3,000`-token shared system prompt, a `10K–75K` dynamic codebase context (mean `40K`), and `5–25` turns (mean `12`) accumulating `500–6,000` input tokens and `100–1,000` output tokens per turn with `1–5s` tool execution round-trip sleeps.

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/inference-perf-agentic.yaml
```

Follow the job's logs until it completes:

```bash
kubectl logs -n ${NAMESPACE} job/inference-perf-agentic -f
```

## Benchmark Results

The results below were measured with the two configurations above on GKE with 4 × `a4-highgpu-8g` (32 × NVIDIA B200, 2 prefill + 2 decode pods) and RoCE RDMA through GPU DRA and DRANET.

### 2K Input / 2K Output, 2048 Concurrent Requests

| Metric | Value |
| :--- | :--- |
| **Output tokens / sec** | 53,377 (~3,336 per decode GPU) |
| **Total tokens / sec** | 106,447 |
| **Request latency P50 / P90** | 72.3 s / 82.9 s |
| **Normalized TPOT P50 / P90** | 36.1 ms / 41.4 ms |
| **Completed requests** | 8,192 / 8,192 |

### Multi-Turn Agentic Workload, 32 Concurrent Sessions

| Metric | Value |
| :--- | :--- |
| **Input tokens / sec** | 102,684 |
| **Total tokens / sec** | 103,536 |
| **TTFT P50 / P90** | 1.55 s / 11.41 s |
| **Inter-token latency P50** | 19.1 ms |
| **Completed requests** | 320 / 320 |

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```

```bash
kubectl delete namespace ${NAMESPACE}
```
