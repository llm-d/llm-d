# Example Observations: gpt-oss-120b on H200 + InfiniBand

Collected with the guide's default configuration, 2026-08. These are environment-specific observations, not official NVIDIA benchmark results.

**Environment.** Two 8×H200 (141 GB HBM3e) nodes with per-GPU InfiniBand NICs (`rdma/ib: 2` per pod). Seed and receiver pods on separate nodes, so every transfer crossed the fabric. Storage rows read from an NFS-backed RWX PVC prewarmed with the checkpoint.

**Stack.** `modelexpress-server:0.5.0` (kubernetes metadata backend), `modelexpress==0.5.0` client on vLLM v0.25.0, `openai/gpt-oss-120b` at TP2 (~61 GB MXFP4 checkpoint, 33.51 GB of materialized weights and 688 tensors per TP rank), Istio sidecars on with the NIXL ports excluded, cudagraphs enabled (`FULL_AND_PIECEWISE`).

## Weight loading

| Path | Weight-load time per rank | Effective rate |
| --- | --- | --- |
| Default loader ← warm NFS RWX PVC | 30.3 s | ~1.1 GB/s |
| ModelExpress P2P (cross-node) | 0.93 s | 285-289 Gbps (~36 GB/s) |
| fastsafetensors | n/a | cannot load MXFP4 |

The P2P number is vLLM's NIXL `Transfer complete` line; three runs landed between 285.5 and 288.6 Gbps. Total receive time including manifest and metadata exchange was 2.9 s. The default-loader row is `Loading weights took` for the same checkpoint from warm NFS. `--load-format=fastsafetensors` hangs at 0% on MXFP4 checkpoints, so no fastsafetensors rows exist for this model; see the [dense + MoE report](./coreweave-h200-llama-3.3-70b.md) for storage-path comparisons on a model it can load.

## JIT compile cache transfer

Same pool, with `MX_ARTIFACT_TRANSFER=1` on the decode pods. The seed compiles once, seals its cache bundles after the engine reports healthy, and receivers install them before model initialization.

| Metric | Cold receiver | With artifact transfer |
| --- | --- | --- |
| torch.compile | 20.1 s | 3.4 s (direct AOT cache load) |
| Engine init (profile, KV cache, warmup) | 94.6 s | 77.8 s |
| Receiver pod-Ready (1→2 scale-out wall clock) | 181 s | 156 s |

The `torch_compile_cache` bundle was 148 MiB and installed in 0.9 s (transfer + unpack). The Triton and FlashInfer bundles were near-empty for this model's kernel path. Cudagraph capture is not a file-backed artifact: it runs per pod on every path and dominates the remaining Ready time.

## Notes

* Artifact identity is digest-gated on the compile configuration and accelerator family. A receiver that cannot discover a compatible bundle compiles cold and logs the miss only at debug level, so verify installs via the `vLLM artifact install complete` log line.
* The seed served the checkpoint from the prewarmed PVC rather than downloading from HuggingFace in-mesh (see the security doc's note on sidecar download stalls). Receiver-side numbers are unaffected by the seed's weight source.
