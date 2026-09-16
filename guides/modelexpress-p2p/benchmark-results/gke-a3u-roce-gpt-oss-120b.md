# Example Observations: gpt-oss-120b on GKE A3 Ultra (H200 + RoCE)

Collected with the guide's default configuration and the `gke` overlay, 2026-08. These are environment-specific observations, not official benchmark results.

**Environment.** Two `a3-ultragpu-8g` nodes (8×H200 141 GB HBM3e, one ConnectX-7 400 Gb/s RoCE NIC per GPU) on GKE v1.35 (Container-Optimized OS). GPUs and NICs are allocated through DRA as GPU+NIC claim pairs (the `gke` overlay). Seed and receiver pods on separate nodes, so every transfer crossed the fabric. The seed served the checkpoint from a prewarmed node-local NVMe cache.

**Stack.** `modelexpress-server:0.5.0` (kubernetes metadata backend), `modelexpress==0.5.0` client on vLLM v0.25.0, `openai/gpt-oss-120b` at TP2 (~61 GB MXFP4 checkpoint, 33.51 GB of materialized weights and 688 tensors per TP rank). UCX settings from the `gke` overlay: `UCX_IB_ROCE_REACHABILITY_MODE=all`, `UCX_IB_NUM_PATHS=4`, `UCX_MAX_RMA_RAILS=4`.

## Weight loading

| Path | Weight-load time per rank | Effective rate |
| --- | --- | --- |
| Default loader ← warm node-local NVMe | 21-22 s | ~1.5 GB/s |
| ModelExpress P2P (cross-node) | 0.70 s | 367-383 Gbps (~48 GB/s) |
| fastsafetensors | n/a | cannot load MXFP4 |

The P2P number is vLLM's NIXL `Transfer complete` line; runs across two node pairs landed between 366.7 and 383.0 Gbps, with 0.70 s / 382.4 Gbps typical for rank 0. The default-loader row is `Loading weights took` for the same checkpoint from the warm local cache. fastsafetensors cannot load MXFP4 checkpoints (see the [H200 + InfiniBand report](./h200-ib-gpt-oss-120b.md)).

A TP1 variant of the same deployment (65.49 GB and 688 tensors in a single rank, one GPU + one NIC) transferred in 1.37 s at 383.0 Gbps.

## JIT compile cache transfer

Same pool, with `MX_ARTIFACT_TRANSFER=1` on the decode pods:

| Metric | Cold receiver | With artifact transfer |
| --- | --- | --- |
| torch.compile | 20.4 s | 3.2-3.5 s (direct AOT cache load) |
| Engine init (profile, KV cache, warmup) | 90.5 s | 73.7-74.8 s |
| Receiver pod-Ready (1→2 scale-out wall clock) | 148 s | 132-136 s |

The `torch_compile_cache` bundle was 148 MiB and installed in 0.6 s (transfer + unpack) — the same bundle size as the InfiniBand environment, as expected for the same model and compile configuration. The Triton and FlashInfer bundles were near-empty for this model's kernel path. As in that environment, cudagraph capture is not a file-backed artifact: it runs per pod on every path and dominates the remaining Ready time.

## Notes

* The `gke` overlay's UCX settings are required on both endpoints; the guide's shared Deployment covers seed and receivers together. Without the overlay, GKE pods start normally but receivers fall back to the disk loader.
