# Qwen/Qwen3-32B Snapshot & Restore Benchmark on SGLang (1×H100)

The benchmark compares initial cold start (pod 1 creating the snapshot on node 1) against fast horizontal scale-out (pod 2 restoring from the snapshot onto a second `a3-highgpu-1g` node, TP=1).

> [!NOTE]
> This guide's value metric is **pod startup latency**, so these figures were collected from Kubernetes pod
> lifecycle events and SGLang logs rather than request-throughput benchmarks
> ([`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) / `inference-perf`). Because snapshot restoration
> resumes the initialized SGLang process and its captured CUDA graphs in GPU memory, steady-state serving
> behavior is expected to match a standard deployment.

**Reference configuration:** `a3-highgpu-1g` (1× NVIDIA H100 80GB, driver `580.126.20`),
GKE `v1.36.4-gke.1247000`, gVisor sandbox, Spot provisioning (`--spot`), GKE Image Streaming enabled (`--enable-image-streaming`);
`Qwen/Qwen3-32B` on SGLang `v0.5.20` at `--mem-fraction-static 0.90`, `--context-length 8192`, `--enable-memory-saver`, and `--enable-weights-cpu-backup` (CUDA graphs enabled).

## Comparing Cold Start to Snapshot Restore

Treat these as one measured data point, not a specification. Phase times measure from **Pod scheduled** (`PodScheduled=True`) through
**serving-ready** (for cold start: when model load, KV cache allocation, and CUDA graph capture complete right before `POST /release_memory_occupation`;
for snapshot restore: when Pod `Ready=True` and `/health` returns HTTP `200`). Both include container startup, and both
exclude **node provisioning time** (`Pod created → Pod scheduled`, which varies by cloud capacity). Download speed, model size,
and GPU will move the figures.

| Metric | Without snapshots (Cold start) | Restore from snapshot |
| :--- | ---: | ---: |
| Pod scheduled → serving-ready | 4m 45s | **20.0s** |
| Weight loads from disk | Yes (61.07 GiB) | **No** |
| Speedup | — | **14.3×** |

<details>
<summary><b><i>Click</i></b> to view the per-phase breakdown</summary>

### Cold start — first pod, creates the snapshot

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | Excluded to isolate pod startup latency from cloud VM provisioning |
| Image pull | 2.0s | kubelet `Pulling` → `Pulled`; time to fetch the image onto the node before the container starts |
| **Pod scheduled → serving-ready** (weights loaded, KV cache allocated, CUDA graphs captured) | **4m 45s** | `PodScheduled=True` (kubelet `Scheduled` event) → `Executing POST /release_memory_occupation...`, measured directly as `285.5s`; the first `121.6s` is container start plus Python and CUDA imports, up to the SGLang log `Load weight begin.` |
| ↳ Model loading — download + load into VRAM | 92.1s | `Load weight begin.` → `Load weight end. elapsed=92.05 s`; `61.07 GiB` of weights, of which the safetensors read into VRAM is `10.1s` |
| ↳ Engine init (profile, KV cache, CUDA graph capture, warmup) | 71.8s | `Load weight end.` → `Executing POST /release_memory_occupation...`; CUDA graph capture is `62.0s` of this and costs `2.97 GiB` |
| `POST /release_memory_occupation` | 11.4s | `Executing POST /release_memory_occupation...` → `POST /release_memory_occupation HTTP/1.1 200 OK`; offloads `61.07 GiB` of weights to host RAM and releases `9.58 GiB` of KV cache and `2.97 GiB` of CUDA graph memory |
| Checkpoint + upload to GCS | 34.1s | `POST /release_memory_occupation HTTP/1.1 200 OK` → `PodSnapshot` `Ready=True`; snapshot is `66.62 GiB` across `componentCount: 2132` objects |
| **Total — Pod scheduled → snapshot `Ready`** (`PodSnapshot` condition `Ready=True`) | **5m 31s** | `285.5s + 11.4s + 34.1s = 331.0s`; excludes node provisioning and image pull |

### Restore — every subsequent pod

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | GCE VM provisioning time (excluded to isolate pod restore latency) |
| Image pull | 4.0s | kubelet `Pulling` → `Pulled`; time to fetch the image onto the node before the container starts |
| Pod scheduled → process restored (rootfs mount, sandbox create, checkpoint stream from GCS) | 15.0s | `PodScheduled=True` → Pod condition `PodRestored=True`; container `startedAt` lands at `7.0s` into this window, so the remaining `8.0s` is GKE streaming the checkpoint from GCS and restoring the process image |
| Process restored → Pod `Ready` | 5.0s | `PodRestored=True` → Pod condition `Ready=True` |
| **Total — Pod scheduled → Pod `Ready`** (serving-ready: readiness probe `GET /health` returns HTTP `200`) | **20.0s** | `PodScheduled=True` → `Ready=True` (`15.0s + 5.0s = 20.0s`); excludes node provisioning; a **14.3×** speedup versus the `4m 45s` cold start |

**Pod scheduled → process restored** measures from `PodScheduled=True` to the Pod condition `PodRestored=True`. The container's
`state.running.startedAt` falls `7.0s` in, once the kubelet has created and started the container and the gVisor sandbox is
running; GKE then streams the checkpoint from GCS and restores the process image (`Snapshot checkpoint created successfully.`),
which accounts for the remaining `8.0s`.

**Process restored → Pod `Ready`** measures from `PodRestored=True` to the Pod condition `Ready=True`, i.e. the readiness probe
(`GET /health` returning HTTP `200`) succeeds. It includes `POST /resume_memory_occupation` (`3.46s`), which copies weights from host RAM
back into VRAM, so a pod reporting `Ready` is serving requests, not merely restored.

</details>
