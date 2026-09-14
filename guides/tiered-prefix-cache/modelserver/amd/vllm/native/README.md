# ROCm vLLM Native Offloading — Triton Swap Threshold

## Background

vLLM's Triton CPU↔GPU swap kernel (`vllm/v1/kv_offload/cpu/swap_blocks_triton.py`) has a
`THRESHOLD_BYTES` of 28 KiB. KV pages larger than this threshold use the Triton fast path
for CPU→GPU read-back; pages below it fall back to a slower copy path.

The threshold must exceed the KV page size for your model and TP configuration:

```
KV page size = block_size × (num_kv_heads / TP) × head_dim × 2 × dtype_bytes
```

**Example — Qwen3-32B at TP=2 (vLLM default block_size=16, bfloat16):**
```
= 16 × (8 / 2) × 128 × 2 × 2 = 32,768 bytes = 32 KiB
```

32 KiB exceeds the stock 28 KiB cutoff, so the fast path is never engaged without the patch.
The threshold is raised to 40 KiB to cover it. Recalculate and update the value in the yaml
if you change the model or TP.

This is a temporary workaround and will be removed once a ROCm-specific fast copy path is enabled.

## CPU offloading

The patch is applied automatically at pod startup via `native/cpu/base/patch-vllm.yaml`.

## FS offloading

The filesystem overlay (`native/fs/base/patch-vllm.yaml`) is still applied, but it omits the
Triton threshold change — the fast path is not currently supported on ROCm for filesystem-backed
offloading.
