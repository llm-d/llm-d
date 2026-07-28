# Configuration matrix — `peakPrefillThroughput` by (model, accelerator)

The `prefix-cache-affinity-filter` plugin needs exactly one hardware/model-specific
number: **`peakPrefillThroughput`** (tokens/sec). It converts a pod's in-flight token
load into a predicted time-to-first-token, which is what gates sticky prefix-affinity
routing. See [the calibration README](./README.md) for how it is measured
(`peakPrefillThroughput = CHUNK_SIZE / median(TTFT)`).

The value depends on **model × accelerator × tensor-parallel size × chunk size**
(`--max-num-batched-tokens`) — not on the model or the accelerator alone. This page is
the reference set for the combinations shipped under `guides/`: **bold** cells are measured;
**‡** cells are proxies borrowed from the closest measured path until that hardware can be
calibrated (see [Filling a cell](#filling-a-cell)).

## Matrix

There is **one row per model-server overlay** shipped by the
[optimized-baseline](../../../optimized-baseline) `modelserver/` paths, listing the model
that overlay actually serves and its tensor-parallel size. vLLM **and** SGLang are separate
rows because the serving engine changes prefill throughput.

| Overlay path | Accelerator · engine | Version | TP | Model (as shipped) | `peakPrefillThroughput` (tok/s) |
|---|---|---|---|---|---|
| `gpu/vllm`         | NVIDIA H100 80 GB · vLLM   | v0.23.0              | 2 | Qwen3-32B               | **15928** |
| `gpu/vllm/gpt-oss` | NVIDIA H100 80 GB · vLLM   | v0.22.0              | 1 | gpt-oss-120B            | **39065** |
| `gpu/sglang`       | NVIDIA H100 80 GB · SGLang | v0.5.13.post1        | 2 | Qwen3-32B               | **30720** |
| `amd/vllm`    | AMD GPU · vLLM             | rocm v0.7.0          | 2 | Qwen3-32B               | 15928 ‡ |
| `amd/sglang`  | AMD GPU · SGLang          | v0.5.13.post1 (rocm) | 2 | Qwen3-32B               | 30720 ‡ |
| `tpu/v6/vllm` | Google TPU v6e · vLLM     | tpu v0.22.0          | 8 | Qwen3-32B               | **26290** |
| `tpu/v7/vllm` | Google TPU v7x · vLLM     | tpu v0.22.0          | 8 | Qwen3-32B               | **27336** |
| `xpu/vllm`    | Intel XPU · vLLM          | xpu v0.7.0           | 1 | Qwen3-0.6B              | 1970 ‡ |
| `cpu/vllm`    | CPU · vLLM (AMX)          | cpu v0.6.0           | 1 | Llama-3.2-3B-Instruct   | **1970** |

- **15928** — the plugin default; measured for the reference path (`gpu/vllm`, Qwen3-32B on
  H100 80 GB, TP=2).
- **30720** — measured for `gpu/sglang` (Qwen3-32B, same H100 80 GB / TP=2): SGLang reaches
  ~1.9× the vLLM prefill throughput on identical hardware, which is exactly why the serving
  engine is its own row.
- **39065** — measured for `gpu/vllm/gpt-oss` (gpt-oss-120B, H100 TP=1): the highest in the table
  despite the largest model, because gpt-oss is a sparse MoE (~5B active params) in MXFP4, so a
  prefill step touches few weights.
- **1970** — measured for `cpu/vllm` (Llama-3.2-3B) on **GCP C3 (Intel Sapphire Rapids, AMX)**,
  bf16. The `llm-d-cpu` image runs the model in bf16, which **requires AMX or AVX512-BF16**.
  Calibrated at `CHUNK_SIZE=2048` (the CPU vLLM chunked-prefill default), not 8192.
- **26290 / 27336** — measured for `tpu/v6/vllm` (TPU v6e, 2x4 = **8 chips**) and `tpu/v7/vllm`
  (TPU v7x, 2x2x1 = **4 chips**), both Qwen3-32B at TP=8.
- The GPU/TPU paths run at the vLLM default `--max-num-batched-tokens=8192`, so calibrate those
  with `CHUNK_SIZE=8192`. **Re-measure** if you change TP, chunk size, quantization, or
  `--max-model-len` — those move the number more than the model identity does.
- **‡ proxy, not measured** — these paths are not yet calibrated, so they borrow the closest
  measured value as a starting point: the **AMD** paths use the same-engine H100 values
  (`amd/vllm` ← `gpu/vllm` 15928, `amd/sglang` ← `gpu/sglang` 30720), and **XPU** uses the
  `cpu/vllm` value (1970). Replace each with a real `calibrate.sh` run on that hardware when available.

**Related (other guides):** the [agentic-serving](../../../agentic-serving) guide ships
`peakPrefillThroughput=16444` for Qwen3-Coder-480B-FP8 on TPU v7x (TP=8) — same accelerator
family, different model, so it is not an optimized-baseline path but is a useful second data point.

## Filling a cell

Deploy that (model, accelerator) per its guide, then run the calibration Job against the
live stack:

```bash
GUIDE_NAME=optimized-baseline \
NAMESPACE=<your-namespace> \
MODEL_NAME=<huggingface/model-id> \
CHUNK_SIZE=<the overlay's --max-num-batched-tokens> \
guides/recipes/router/calibration/calibrate.sh
```

Copy the printed `peakPrefillThroughput` into both (a) the cell above and (b) the
`prefix-cache-affinity-filter` parameters in your guide's router values file, then
`helm upgrade` and restart the EPP (see the calibration README).
