# Configuration matrix — `peakPrefillThroughput` by (model, accelerator)

The `prefix-cache-affinity-filter` plugin needs exactly one hardware/model-specific
number: **`peakPrefillThroughput`** (tokens/sec). It converts a pod's in-flight token
load into a predicted time-to-first-token, which is what gates sticky prefix-affinity
routing. See [the calibration README](./README.md) for how it is measured
(`peakPrefillThroughput = CHUNK_SIZE / median(TTFT)`).

The value depends on **model × accelerator × tensor-parallel size × chunk size**
(`--max-num-batched-tokens`) — not on the model or the accelerator alone. This page is
the reference set for the combinations shipped under `guides/`: measured cells give the
value; `calibrate` cells are not yet measured and are filled with the one command in
[Filling a cell](#filling-a-cell).

## Matrix

There is **one row per model-server overlay** shipped by the
[optimized-baseline](../../../optimized-baseline) `modelserver/` paths, listing the model
that overlay actually serves and its tensor-parallel size. vLLM **and** SGLang are separate
rows because the serving engine changes prefill throughput.

| Overlay path | Accelerator · engine | TP | Model (as shipped) | `peakPrefillThroughput` (tok/s) |
|---|---|---|---|---|
| `gpu/vllm`    | NVIDIA H100 80 GB · vLLM   | 2 | Qwen3-32B               | **15928** |
| `gpu/sglang`  | NVIDIA H100 80 GB · SGLang | 2 | Qwen3-32B               | **30720** |
| `amd/vllm`    | AMD GPU · vLLM             | 2 | Qwen3-32B               | `calibrate` |
| `amd/sglang`  | AMD GPU · SGLang          | 2 | Qwen3-32B               | `calibrate` |
| `tpu-v6/vllm` | Google TPU v6e · vLLM     | 8 | Qwen3-32B               | **26290** |
| `tpu-v7/vllm` | Google TPU v7x · vLLM     | 8 | Qwen3-32B               | **27336** |
| `hpu/vllm`    | Intel Gaudi / HPU · vLLM  | 1 | Qwen3-8B                | `calibrate` |
| `xpu/vllm`    | Intel XPU · vLLM          | 1 | Qwen3-0.6B              | `calibrate` |
| `cpu/vllm`    | CPU · vLLM (AMX)          | 1 | Llama-3.2-3B-Instruct   | **1970** † |

- **15928** — the plugin default; measured for the reference path (`gpu/vllm`, Qwen3-32B on
  H100 80 GB, TP=2).
- **30720** — measured for `gpu/sglang` (Qwen3-32B, same H100 80 GB / TP=2): SGLang reaches
  ~1.9× the vLLM prefill throughput on identical hardware, which is exactly why the serving
  engine is its own row.
- **1970 †** — measured for `cpu/vllm` (Llama-3.2-3B) on **GCP C3 (Intel Sapphire Rapids, AMX)**,
  bf16. † The `llm-d-cpu` image runs the model in bf16, which **requires AMX or AVX512-BF16**.
  Calibrated at `CHUNK_SIZE=2048` (the CPU vLLM chunked-prefill default), not 8192.
- **26290 / 27336** — measured for `tpu-v6/vllm` (TPU v6e, 2x4 = **8 chips**) and `tpu-v7/vllm`
  (TPU v7x, 2x2x1 = **4 chips**), both Qwen3-32B at TP=8.
- The GPU/TPU paths run at the vLLM default `--max-num-batched-tokens=8192`, so calibrate those
  with `CHUNK_SIZE=8192`. **Re-measure** if you change TP, chunk size, quantization, or
  `--max-model-len` — those move the number more than the model identity does.

**Related (other guides):** the [agentic-serving](../../../agentic-serving) guide ships
`peakPrefillThroughput=16444` for Qwen3-Coder-480B-FP8 on TPU v7x (TP=8) — same accelerator
family, different model, so it is not an optimized-baseline path but is a useful second data point.

**Larger models you may want to support** (not shipped as overlays): gpt-oss-120B and
Llama-3-70B were benchmarked on H100 (TP=2) during the PR #1651 review; deploy them on the
`gpu/vllm` path and run `calibrate.sh` when adopting them.

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

## Plan / priority

The 5 GCP-reachable paths are measured. The 4 remaining are vendor-hardware-gated:

- **`amd/vllm`, `amd/sglang`, `xpu/vllm`, `hpu/vllm`** — require AMD ROCm / Intel Max / Gaudi
  hardware that isn't available on GCP, so these are best filled by whoever runs that vendor's
  guide. Calibrate `hpu`/`xpu` only if you serve those models for real (the small placeholder
  models make it low-value otherwise).

Done: `gpu/vllm` (15928), `gpu/sglang` (30720) on H100; `cpu/vllm` (1970) on C3 Sapphire Rapids;
`tpu-v6/vllm` (26290) on v6e, `tpu-v7/vllm` (27336) on v7x.

Until a path is measured, the default (`15928`) is a reasonable starting point for similar shapes
(dense ~30 B on ~2×80 GB accelerators). It will be off where the compute-to-HBM-bandwidth ratio
differs sharply (TPU, SGLang, very large or very small models) — which is exactly the gap
calibration closes.
