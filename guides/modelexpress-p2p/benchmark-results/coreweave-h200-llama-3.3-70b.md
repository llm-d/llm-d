# Example Observations: CoreWeave H200 + InfiniBand

Collected with the procedure in [measuring-storage-paths](../measuring-storage-paths.md). The measurements below are observations from one CoreWeave H200 + InfiniBand environment, included to help operators understand what to measure. They are not official NVIDIA benchmark results. NVIDIA plans to follow up with official benchmark data.

**Why this model** The example measurement uses a checkpoint that loads through both the P2P and storage-backed paths. MXFP4 models (for example, `gpt-oss-120b`) were not used because `--load-format=fastsafetensors` hangs at 0% on MXFP4 in this validation setup. Llama-3.3-70B is dense bf16 (the fastsafetensors paper's own reference model), and at TP2 it costs only 2 GPUs per replica. This lets you run the procedure on a modest GPU budget and scale the fan-out when you want a larger local measurement.

Measurement paths:

* Plain HuggingFace cold download: every pod pulls ~140 GB from HuggingFace and runs the default safetensors deserializer. This path exercises per-pod egress plus disk-to-HBM loading.
* fastsafetensors off prewarmed NFS: a one-shot Job downloads the checkpoint once into an NFS RWX PVC. Every pod mounts it read-only and serves `--load-format=fastsafetensors`. This path measures one shared download with N storage readers.
  In theory it can use cuFile/GDS to DMA from storage straight into HBM. In practice GDS needs an RDMA-capable mount, and on the test cluster (CoreWeave's VAST-backed NFS) the PVC mounts as TCP NFS. So it ran on the `nogds` POSIX-pread path. See the GDS note under the results table.
* fastsafetensors off warm local NVMe (optional): each pod's init copies the warm NFS checkpoint onto node-local NVMe (untimed prime), then loads from there. GDS-via-CUDA-P2PDMA needs `nvidia-fs` or a P2PDMA-capable node. Neither bound on the test cluster, so the timed read used a plain non-GDS pread. This path also re-primes each node. Skip it unless your nodes have working local-NVMe GDS or you specifically want a local-NVMe measurement.
* ModelExpress P2P: the seed pod downloads once and publishes its HBM as a NIXL source. Every other pod pulls weights HBM→HBM over RDMA, and none of them touch disk. No shared storage, no GDS, no cuFile, just the fabric.

**Measurement controls:** (1) Every "warm" path does its download/prime in a Job or initContainer that is _not_ part of the timed `vllm serve`, just as the P2P path excludes the seed pod's HuggingFace download. (2) Hold cudagraph/compile constant across paths (either layer `shared-compile-cache` on all of them, or set `--enforce-eager` on all) so the weight path stays the main variable. (3) Report `Loading weights took` median and max, plus the 1→N scale-out wall clock, and note whether GDS actually engaged.

Example observation from CoreWeave (8×H200 + InfiniBand nodes, `meta-llama/Llama-3.3-70B-Instruct`, TP2, ~70.6 GB of weights per TP worker, warm storage; the NFS path ran on CoreWeave's VAST-backed `shared-vast` StorageClass), 2026-05:

| Path | Weight-load time | Effective rate | Total pod-Ready (1→2) | Transport | Egress | Shared storage |
| --- | --- | --- | --- | --- | --- | --- |
| Plain HF cold download | download-bound (~140 GB/pod) | HF bandwidth | — | HF → disk → HBM, default | **Nx** | no |
| Default loader ← prewarmed NFS | 22.6 s | ~3.1 GB/s | — | NFS → HBM, default | 1x | yes (RWX PVC) |
| fastsafetensors ← prewarmed NFS | 11.6 s | ~6.1 GB/s | ~157 s | NFS → HBM, fastsafetensors (no GDS) | 1x | yes (RWX PVC) |
| fastsafetensors ← warm local NVMe | 30.2 s | ~2.3 GB/s | — | NVMe → HBM, fastsafetensors (no GDS) | 1x | NFS source + per-node copy |
| ModelExpress P2P | 2.7 s | ~210 Gbps (~26 GB/s) | 152 s | peer HBM → HBM, RDMA (NIXL) | 1x | no |

The leading column is per-TP-worker weight-load time (vLLM's `Loading weights took` for the storage-backed paths; the NIXL `Transfer complete` line for P2P). Total pod-Ready is the end-to-end 1→2 scale-out wall clock, and it also includes engine init and `torch.compile`/cudagraph capture.

> **About GDS on these numbers (important):** none of the storage tests above actually engaged GPUDirect Storage, even though the cluster has an IB fabric. On the CoreWeave test cluster the NFS PVC (VAST-backed) mounts as **NFSv3 over TCP** (`proto=tcp,nconnect=32`), and cuFile/GDS cannot bind a TCP-NFS mount. So fastsafetensors ran on its `nogds` POSIX-pread path.
> We tried forcing it: a custom StorageClass with `proto=rdma` **provisions and binds, but the mount itself times out** (`MountVolume.SetUp ... DeadlineExceeded`). NFSoRDMA is not serviceable on this export unless the cloud provider enables it, so GDS-over-NFS is **not** achievable purely self-serve here. Local NVMe did not help either: `nvidia-fs` is not loaded, and CUDA P2PDMA did not bind, so the local-NVMe test was _slower_ (30.2 s) than NFS. A single-threaded pread off NVMe loses to the NFS mount's `nconnect=32` parallel TCP read.
> In this environment, the storage-backed measurements used the available POSIX read paths. With GDS enabled, storage-backed results may change a lot. The P2P path does not need cuFile, GDS, or a special mount, because receivers pull from peer HBM over RDMA.

In this environment, the P2P transfer moved a 70.6 GB TP shard HBM→HBM in 2.7 s at about 210 Gbps. The measured storage-backed paths ranged from 11.6 s to 30.2 s for the same shard size, with the GDS limitations noted above. Treat these as environment-specific observations. Storage configuration, GDS availability, filesystem behavior, model format, and vLLM settings can change results a lot.

> **Why the scale-out wall clock can move less than weight-transfer time:** for a 70B model, end-to-end pod-Ready time includes vLLM engine init and `torch.compile`/cudagraph capture. This stays the same across paths when you hold measurement controls constant. Weight loading is one slice of total Ready time, and that slice becomes more visible for larger models, wider fan-outs, and `--enforce-eager` workloads (RL rollouts) where there is no cudagraph capture cost. To also reduce compile cost, [reuse JIT caches across pods](../compile-cache.md).
>
> The optional LOTA / object-cache test was not run (it needs a provisioned object bucket and S3 credentials). Its path is documented as a stretch row above.

## Additional MoE observation

MoE checkpoints stress storage-deserialize paths differently from dense checkpoints, because they contain many small per-expert tensors. The same validation environment also ran `meta-llama/Llama-4-Scout-17B-16E-Instruct` (108.6B total / 17B active, bf16, TP4, ~57 GB of weights per TP worker, `--enforce-eager`):

| Path | Weight-load time | Notes |
| --- | --- | --- |
| Default loader ← prewarmed NFS | 211 s | 1047 small expert tensors on the storage-deserialize path |
| fastsafetensors + GDS ← prewarmed NFS | failed to load | crashes at ~4/13 shards, CUDA OOM from the fastsafetensors TP VRAM spike ([vllm#29403](https://github.com/vllm-project/vllm/issues/29403)), even at `--gpu-memory-utilization=0.80` |
| ModelExpress P2P ← peer HBM | ~2.0–2.5 s (57 GB/worker, ~180–232 Gbps) | similar transfer range to the dense case |

Two operational notes:

* The observed P2P transfer time tracked bytes rather than tensor count. Receivers pulled already-materialized tensors from the seed.
* P2P avoids storage-loader-specific behavior on receiver pods. In this run, fastsafetensors could not load the MoE at TP4. `--load-format=fastsafetensors` is also known to hang on MXFP4 in this setup (that is why `gpt-oss` is not used here). If the seed can load the model, receivers get the materialized tensors over RDMA.
