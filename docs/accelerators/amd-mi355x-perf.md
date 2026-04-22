# AMD MI355X Performance Reference (Llama-3.3-70B-FP8-KV, TP=8)

This document captures performance data for `amd/Llama-3.3-70B-Instruct-FP8-KV` running on a single 8×AMD Instinct MI355X (gfx950) node using `ghcr.io/llm-d/llm-d-rocm:v0.6.0`. It is intended to help operators size deployments and decide which knobs to spend tuning effort on.

All numbers are from `vllm bench serve --backend vllm --dataset-name random` against a standalone vLLM container started with the same image llm-d uses (`ghcr.io/llm-d/llm-d-rocm:v0.6.0`). Each config was run once with `--seed 1` (no multi-run averaging — the cross-config variance reported below already exceeds typical run-to-run noise).

---

## Hardware

| Property | Value |
|---|---|
| GPUs | 8× AMD Instinct MI355X (gfx950) |
| VRAM | 288 GB / GPU (2304 GB total) |
| Container | `ghcr.io/llm-d/llm-d-rocm:v0.6.0` (vLLM 0.15.1, ROCm 7.x) |
| `PYTORCH_ROCM_ARCH` | includes `gfx942;gfx950` (no rebuild needed) |

## Server config

```
vllm serve amd/Llama-3.3-70B-Instruct-FP8-KV \
  --tensor-parallel-size 8 \
  --gpu-memory-utilization 0.85 \
  --block-size 128 \
  --max-model-len <varied> \
  --disable-uvicorn-access-log
```

## Throughput summary

For each workload, the best result across all swept parameters (gpu-mem-util ∈ {0.85, 0.95}, block-size ∈ {64, 128, 256}, max-num-seqs ∈ {256, 512}, max-model-len ∈ {32k, 64k, 128k}). Cross-config Δ at the same workload is reported in the rightmost column.

### Short context (1024 input, 256 output)

| Concurrency | Output tok/s | Total tok/s | TTFT (mean) | ITL (median) | Cross-config Δ |
|---|---|---|---|---|---|
| 16 | 1320.8 | 6598.8 | 343 ms | 10.3 ms | 2.7% |
| 64 | 1519.2 | 25820.6 | 2440 ms | 37.3 ms (TPOT) | 0.7% |
| 128 | 2985.0 | 26853.0 | 1505 ms | 39.2 ms (TPOT) | 0.2% |
| 256 | 3347.9 | 30118.4 | 2296 ms | 72.5 ms (TPOT) | 0.2% |

### Long context

| Workload | Concurrency | Output tok/s | Total tok/s | TTFT (mean) | TPOT/ITL (median) |
|---|---|---|---|---|---|
| Decode-heavy (4k in × 4k out) | 4  | 353.6  | 707.1 | 369 ms | 11.3 ms |
| Decode-heavy (4k in × 4k out) | 16 | 1233.0 | 2466 | 871 ms | 12.8 ms |
| Decode-heavy (4k in × 4k out) | 32 | 2230.0 | 4459 | 1204 ms | 14.1 ms |
| Long-both (16k in × 2k out) | 4  | 246.8  | 2221 | 1.77 s | 21.0 ms |
| Long-both (16k in × 2k out) | 16 | 717.1  | 6454 | 3.10 s | 21.2 ms |
| Long-both (16k in × 2k out) | 32 | 1112.6 | 10013 | 4.44 s | 27.7 ms |
| Prefill-heavy (32k in × 128 out) | 4 | 48.8 | 12533 | 4.29 s | — |
| Prefill-heavy (64k in × 128 out) | 2 | 15.5 | 7959 | 9.09 s | — |

Decode throughput scales near-linearly from concurrency 16 → 32 (1.81×), indicating that compute (not KV-cache space) is the binding constraint at these workloads on MI355X.

## What knobs to tune (and which to leave alone)

The matrix above is the consolidated result of **51 sweep configurations** across:

* `--gpu-memory-utilization` ∈ {0.85, 0.90, 0.95}
* `--block-size` ∈ {64, 128, 256}
* `--max-num-seqs` ∈ {256, 512}
* `--max-model-len` ∈ {32 768, 65 536, 131 072}

For Llama-3.3-70B-Instruct-FP8-KV at TP=8 on MI355X, these knobs **do not produce performance improvements above the 1–3 % range** in any tested workload. The recommended defaults from `guides/pd-disaggregation/ms-pd/values_amd.yaml` and `guides/inference-scheduling/ms-inference-scheduling/values_amd.yaml` (i.e. `gpu-memory-utilization=0.85`, `block-size=128`) are within noise of the best configuration we observed and should not be changed without a model-specific or workload-specific reason.

The reason MI355X's larger VRAM (288 GB / GPU vs MI300X's 192 GB / GPU) does not turn into measurably higher throughput at these workloads is that **even at concurrency 256 with 1k input + 256 output, the active KV cache fits in well under 0.85 of MI355X's VRAM**. The model is compute-bound. Operators should expect MI355X's extra capacity to manifest as:

1. **Longer maximum context length** (`--max-model-len`) without OOM. We verified that `--max-model-len 131072` (the model's `max_position_embeddings`) starts cleanly and serves correctly at TP=8 on MI355X with no observable throughput cost vs `--max-model-len 32000`.
2. **Headroom for larger models** that do not fit on MI300X at the same TP.
3. **Headroom for more replicas at a smaller TP**, or denser KV-cache packing for prefix-caching-heavy workloads.

## Recommendation for MI355X operators

* If your serving needs are bounded by ≤ 32k context: the existing `values_amd.yaml` defaults are appropriate.
* If you serve long-context workloads (≥ 32k): set `--max-model-len 131072` and the throughput numbers above apply directly.
* Do not invest tuning time in `--gpu-memory-utilization`, `--block-size`, or `--max-num-seqs` for this model on MI355X. Spend it on workload-shaping instead (chunked prefill, prefix caching, request batching).

## Validation environment

* Single 8×MI355X node (Tensorwave `mia1-p01-g07`)
* `ghcr.io/llm-d/llm-d-rocm:v0.6.0` standalone container (Helm/K8s not used for these measurements)
* `amd/Llama-3.3-70B-Instruct-FP8-KV` weights mounted from a shared filesystem
* Date: April 2026

## Reproducer

```bash
docker run -d --name llmd-bench \
  --device=/dev/kfd --device=/dev/dri --network=host --ipc=host --shm-size=128g \
  --group-add video --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  -v /path/to/models:/models:ro \
  ghcr.io/llm-d/llm-d-rocm:v0.6.0 \
    --model /models/Llama-3.3-70B-Instruct-FP8-KV \
    --port 8000 --tensor-parallel-size 8 \
    --gpu-memory-utilization 0.85 \
    --block-size 128 \
    --max-model-len 131072 \
    --disable-uvicorn-access-log

docker exec llmd-bench python3 -m vllm.entrypoints.cli.main bench serve \
  --backend vllm \
  --model /models/Llama-3.3-70B-Instruct-FP8-KV \
  --base-url http://127.0.0.1:8000 \
  --dataset-name random --random-input-len 4096 --random-output-len 4096 \
  --num-prompts 128 --max-concurrency 32 --seed 1
```
