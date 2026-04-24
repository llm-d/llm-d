# Well-lit Path: precise prefix cache aware routing

[![Nightly - Precise Prefix Cache E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml)

## Overview

This guide configures the inference scheduler to route on precise per-pod KV-cache state rather than request-traffic heuristics. Each vLLM pod publishes [KV-cache events](https://github.com/vllm-project/vllm/issues/16669) over ZMQ; the scheduler subscribes, builds an index keyed by block hash, and scores candidate pods by the fraction of an incoming request's prefix that is already resident.

Versus the approximate `prefix-cache-scorer` used in the [optimized-baseline guide](../optimized-baseline), the precise path trades a bit more plumbing (ZMQ ingress, tokenizer sidecar) for hit rates that hold up under high throughput and long shared prefixes — see the benchmark section below.

## Default configuration

| Parameter | Value |
|-----------|-------|
| Model | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas | 8 |
| Tensor Parallelism | 2 |
| GPUs per replica | 2 |
| Total GPUs | 16 |
| vLLM `--block-size` | 64 (must match scorer `tokenProcessorConfig.blockSize`) |
| Scheduler image | `ghcr.io/llm-d/llm-d-inference-scheduler:v0.8.0-rc.1` |

`v0.8.0-rc.1` is the first release carrying [llm-d-inference-scheduler#862](https://github.com/llm-d/llm-d-inference-scheduler/pull/862) (data-layer `EndpointExtractor`). Swap to the GA tag once it lands.

### Supported hardware backends

| Backend | Directory | Default model | Notes |
|---------|-----------|---------------|-------|
| NVIDIA CUDA | [`modelserver/nvidia-gpu/vllm/`](modelserver/nvidia-gpu/vllm/) | Qwen/Qwen3-32B | Headline config |
| AMD ROCm | [`modelserver/amd-gpu/vllm/`](modelserver/amd-gpu/vllm/) | Qwen/Qwen3-32B | |
| Intel XPU | [`modelserver/xpu/vllm/`](modelserver/xpu/vllm/) | Qwen/Qwen3-0.6B | CI-sized; update scheduler `modelName` for real use |
| Intel Gaudi (HPU) | [`modelserver/hpu/vllm/`](modelserver/hpu/vllm/) | Qwen/Qwen3-8B | Uses `--block-size=128`; update scorer `blockSize` to match |
| Google TPU v6e | [`modelserver/tpu-v6/vllm/`](modelserver/tpu-v6/vllm/) | Llama-3.1-70B-Instruct | |
| Google TPU v7 | [`modelserver/tpu-v7/vllm/`](modelserver/tpu-v7/vllm/) | Qwen3-Coder-480B-FP8 | |
| CPU | [`modelserver/cpu/vllm/`](modelserver/cpu/vllm/) | Llama-3.2-3B-Instruct | CI-sized |

> **Non-headline overlays**: overlays that run a non-default model (CPU/XPU/HPU/TPU) are CI-smoke-test configurations. For precise prefix cache scoring to match reality, the `tokenizer` `modelName` and the scorer's `indexerConfig.tokenizersPoolConfig.modelName` in [`scheduler/precise-prefix-cache-aware.values.yaml`](scheduler/precise-prefix-cache-aware.values.yaml) must match the model the overlay deploys. HPU and anything that tunes `--block-size` also requires updating `tokenProcessorConfig.blockSize` on the scheduler side.

## Prerequisites and installation

For prerequisites, installation, verification, and cleanup instructions, see the [guide installation docs](../01_installing_a_guide.md).

This guide uses the **central ZMQ** mode by default: a single scheduler replica binds `tcp://*:5557`, and vLLM pods connect in as ZMQ publishers. To run multiple scheduler replicas (active-active) or to have the scheduler dial per-pod endpoints, see [pod-discovery.md](pod-discovery.md).

### Quick install summary

```bash
export NAMESPACE=llm-d-precise
kubectl create namespace ${NAMESPACE}
# HuggingFace token — Qwen/Qwen3-32B is public but the UDS tokenizer sidecar
# reads HF_TOKEN to reach gated tokenizers, so create the secret regardless.
kubectl -n ${NAMESPACE} create secret generic llm-d-hf-token --from-literal=HF_TOKEN="${HF_TOKEN}"

# 1. Scheduler (standalone, no gateway). Use `inferencepool` chart for gateway mode.
helm install precise-prefix-cache-aware-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
  --set provider.name=none \
  -n ${NAMESPACE} --version v1.4.0

# 2. Model server
kustomize build guides/precise-prefix-cache-aware/modelserver/nvidia-gpu/vllm/ | \
  kubectl apply -n ${NAMESPACE} -f -
```

The release name `precise-prefix-cache-aware-scheduler` is load-bearing: the vLLM patches hardcode `KV_EVENTS_ENDPOINT=tcp://<release>-epp.<ns>.svc.cluster.local:5557`. If you use a different release name, patch `KV_EVENTS_ENDPOINT` in your overlay or pass a kustomize patch on top.

## How it works

1. **vLLM pods publish KV-cache events** — each pod runs `vllm serve ... --kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):8000@<model>"}'`. On every KV block allocation/eviction, vLLM emits a ZMQ message.
2. **Scheduler subscribes** — in central mode the scheduler's scorer binds `tcp://*:5557` and all vLLM publishers connect in. A single `kv@`-prefixed topic filter passes all events through.
3. **Index is keyed by block hash** — the scorer hashes tokens using `blockSize=64` + `hashSeed="42"` (must match vLLM's `PYTHONHASHSEED=42` env var) to produce the same block IDs vLLM emits. Incoming requests are tokenized via the UDS tokenizer sidecar, hashed with the same parameters, and looked up in the index.
4. **Scoring** — the `precise-prefix-cache-scorer` returns the fraction of the request's prefix blocks that are resident on each candidate pod. The `max-score-picker` routes to the highest-scoring pod.

The `tokenizer` plugin and the scorer's internal `tokenizersPoolConfig` both point at `/tmp/tokenizer/tokenizer-uds.socket` — a UDS tokenizer sidecar (`ghcr.io/llm-d/llm-d-uds-tokenizer`) owns tokenizer model downloads and caching, keeping tokenization out of the EPP main container.

## Verification

The shared verification steps are in [`02_verifying_a_guide.md`](../02_verifying_a_guide.md). Feature-specific checks:

Inspect the scorer's per-request scores:
```bash
kubectl logs -l app=precise-prefix-cache-aware-scheduler-epp -n ${NAMESPACE} --tail 200 \
  | grep "Calculated score" | grep "precise-prefix-cache-scorer"
```

On the first request through a fresh deployment you should see `score: 0` for all pods. Send the *same* long prompt again — the pod that served it first should now return `score: 1`, confirming that its KV blocks were indexed via the event stream.

## Benchmarking

See [`03_benchmarking_a_guide.md`](../03_benchmarking_a_guide.md) for the general benchmark runner. The guide's workload template is [`benchmark-templates/guide.yaml`](benchmark-templates/guide.yaml) — a shared-prefix synthetic with 150 groups × 5 unique questions × 6000-token system prompts.

### Benchmark report

16× H100, 8 model-server replicas × TP=2, Qwen/Qwen3-32B, `shared_prefix` workload. Representative stage at sustained 60 req/s shown below.

<details>
<summary><b>Click</b> to view the stage report</summary>

```yaml
metrics:
  latency:
    request_latency:
      mean: 63.34
      p50: 60.84
      p90: 75.70
      p99: 77.97
      units: s
    time_to_first_token:
      mean: 0.192
      p50: 0.178
      p90: 0.260
      p99: 0.564
      units: s
    time_per_output_token:
      mean: 0.063
      p50: 0.061
      p90: 0.075
      p99: 0.078
      units: s/token
  requests:
    failures: 0
    input_length:
      mean: 7584
    output_length:
      mean: 937
    total: 1500
  throughput:
    requests_per_sec: 14.87
    output_tokens_per_sec: 13932.0
    total_tokens_per_sec: 126727.5
  time:
    duration: 24.92
```

</details>

### Precise vs. a plain Kubernetes service

Graphs below are from `inference-perf --analyze` comparing the precise path to a stock Kubernetes service routing directly to the vLLM pods.

<img src="./benchmark-results/latency_vs_qps.png" width="900" alt="Latency vs QPS">
<img src="./benchmark-results/throughput_vs_qps.png" width="450" alt="Throughput vs QPS">

Stage at `rate=60`:

| Metric | k8s (Mean) | llm-d precise (Mean) | Δ% vs k8s |
| :--- | :--- | :--- | :--- |
| Requests/sec | 5.73 | 14.87 | **+159.5%** |
| Output tokens/sec | 5,362.16 | 13,931.99 | **+159.8%** |
| Total tokens/sec | 48,780.02 | 126,727.46 | **+159.8%** |
| Request latency (s) | 105.41 | 63.34 | **-39.9%** |
| TTFT (s) | 34.91 | 0.19 | **-99.5%** |
| Inter-token latency (ms) | 70.42 | 63.07 | **-10.4%** |

## Customization

For general guidance on customizing a guide, see [`04_customizing_a_guide.md`](../04_customizing_a_guide.md).

Feature-specific knobs:
- **Block size / hash seed** — `tokenProcessorConfig.{blockSize,hashSeed}` in [`scheduler/precise-prefix-cache-aware.values.yaml`](scheduler/precise-prefix-cache-aware.values.yaml) must match vLLM's `--block-size` and `PYTHONHASHSEED` env var. Changing one side requires changing the other.
- **Model** — the `tokenizer` `modelName` and scorer `tokenizersPoolConfig.modelName` in the scheduler values file must match the model the vLLM overlay deploys.
- **Scorer weights** — `schedulingProfiles[default].plugins` in the scheduler values file — the precise scorer's default weight is `2.0`, paired 1:1 with kv-cache-utilization and queue scorers.
- **Speculative indexing** — set `indexerConfig.speculativeIndexing: true` to have the scorer pre-index blocks for the routing target immediately after `PreRequest`, closing the gap between the routing decision and the first KV-event arrival.
- **Pod discovery / active-active** — see [`pod-discovery.md`](pod-discovery.md).
