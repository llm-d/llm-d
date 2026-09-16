# Qwen/Qwen3-32B Snapshot & Restore Benchmark on vLLM (1×H100)

The benchmark compares initial cold start (pod 1 creating the snapshot on node 1) against fast horizontal scale-out (pod 2 restoring from the snapshot onto a second `a3-highgpu-1g` node, TP=1).

> [!NOTE]
> This guide's value metric is **pod startup latency**, so these figures were collected from Kubernetes pod
> lifecycle events and vLLM logs rather than request-throughput benchmarks
> ([`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) / `inference-perf`). Because snapshot restoration
> resumes the initialized vLLM process and its captured CUDA graphs in GPU memory, steady-state serving
> behavior is expected to match a standard deployment.

**Reference configuration:** `a3-highgpu-1g` (1× NVIDIA H100 80GB HBM3, driver `580.126.20`),
GKE `v1.36.4-gke.1082000`, gVisor sandbox, Spot provisioning (`--spot`), GKE Image Streaming enabled (`--enable-image-streaming`);
`Qwen/Qwen3-32B` on vLLM `v0.26.0` at `--gpu-memory-utilization 0.90` and `--max-model-len 8192` (CUDA graphs enabled).

## Comparing Cold Start to Snapshot Restore

Treat these as one measured data point, not a specification. Phase times measure from **Pod scheduled** (`PodScheduled=True`) through
**serving-ready** (for cold start: when model load, KV cache allocation, and CUDA graph capture complete right before `engine.sleep(level=1)`;
for snapshot restore: when Pod `Ready=True` and `/v1/models` returns HTTP `200`). Both include image pull/mount and container startup while
excluding **node provisioning time** (`Pod created → Pod scheduled`, which varies by cloud capacity). Download speed, model size, and GPU
will move the figures.

| Metric | Without snapshots (Cold start) | Restore from snapshot |
| :--- | ---: | ---: |
| Pod scheduled → serving-ready | 4m 38s | **18.0s** |
| Weight loads from disk | Yes (61.03 GiB) | **No** |
| Speedup | — | **15.4×** |

<details>
<summary><b><i>Click</i></b> to view the per-phase breakdown</summary>

### Cold start — first pod, creates the snapshot

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | Excluded to isolate pod startup latency from cloud VM provisioning |
| Image pull (new node, image streaming enabled, 8.91 GB) | 1.8s | Kubelet `Pulling` → `Pulled` event (`containerd` mounts rootfs over `gcfs`; `69s` without Image Streaming) |
| Python & CUDA library imports (on-demand via `gcfs`) | 73s | Container `startedAt` → vLLM log `Loading model from scratch...` |
| Model loading — download + load into VRAM (61.03 GiB) | 130s | `Loading model from scratch...` → `Model loading took 61.03 GiB and 130.20 seconds` |
| &nbsp;&nbsp;&nbsp;&nbsp;↳ of which, safetensors read into VRAM | 43s | `Loading weights took 43.41 seconds` |
| Engine init (profile, KV cache, CUDA graph capture [9s / 1.86 GiB], warmup) | 73s | `Model loading took...` → `Executing engine.sleep(level=1) to release physical VRAM...` |
| **Pod scheduled → serving-ready (checkpoint triggered)** | **4m 38s** | `PodScheduled=True` → `Executing engine.sleep(level=1)...` (`1.8s + 73s + 130s + 73s = 277.8s`) |
| `engine.sleep(level=1)`: weights offloaded to host RAM | 61.68 GiB | `CuMemAllocator: sleep freed 70.20 GiB memory in total, of which 61.68 GiB is backed up in CPU` |
| `engine.sleep(level=1)`: KV cache discarded | 8.52 GiB | `...and the rest 8.52 GiB is discarded directly` (not captured in snapshot) |
| GPU memory still in use after sleep | 2.90 GiB | `Sleep mode freed 72.92 GiB memory, 2.9 GiB memory is still in use` (includes `1.86 GiB` CUDA graphs) |
| Time to fall asleep | 17.4s | `Executing engine.sleep(level=1)...` → `It took 17.412627 seconds to fall asleep` |
| Checkpoint + upload to GCS (`componentCount: 2104`) | **34.0s** | `Triggering snapshot checkpoint...` → `gVisor checkpoint completed successfully` / `PodSnapshot` `Ready=True` |
| **Total Pod scheduled → snapshot `Ready`** | **5m 29s** | `PodScheduled=True` → `PodSnapshot` condition `Ready=True` (`277.8s + 17.4s + 34.0s = 329.2s`) |
| Snapshot size in GCS | **65.73 GiB** | Sum of objects under `gs://<bucket>/<snapshot-uid>/` (`pages.img` [`65.72 GiB`] + metadata) |

A standalone microbenchmark on the same H100 node isolates where the **17.4s** goes: allocating `61.68 GiB` of pinned host memory
(`pin_memory=True`) took **9.2s**, while the `cudaMemcpy` device-to-host transfer took only **2.3s** (`26.5 GiB/s`). The remaining ~6s is the
per-tensor `unmap_and_release` loop, `gc.collect()`, and `torch.cuda.empty_cache()` that `CuMemAllocator.sleep()` performs after the copy.
Host page-locking — not PCIe bandwidth — dominates, which is why `level=1` (offload weights to host RAM) costs seconds where `level=2`
(discard weights) would not. gVisor's GCS client then streams `pages.img` directly from host RAM to GCS using parallel composite uploads
(`componentCount: 2104` chunks of `32 MiB`), completing the `65.73 GiB` checkpoint in **34.0s**.

### Restore — every subsequent pod

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | GCE VM provisioning time (excluded to isolate pod restore latency) |
| Pod scheduled → Pod restored (image mount + GCS snapshot stream) | **14.0s** | `PodScheduled=True` (container `startedAt` at `+6.0s`) → Pod condition `PodRestored=True` |
| Pod restored → Pod `Ready` | **4.0s** | `PodRestored=True` (`Process restored from snapshot checkpoint`) → Pod condition `Ready=True` (includes `2.67s` `engine.wake_up()`) |
| **Pod scheduled → Pod `Ready` (serving-ready)** | **18.0s** | `PodScheduled=True` → Pod condition `Ready=True` (`14.0s + 4.0s = 18.0s`; **15.4×** speedup vs `4m 38s`) |

</details>
