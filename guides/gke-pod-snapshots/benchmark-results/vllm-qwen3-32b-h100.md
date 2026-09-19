# Qwen/Qwen3-32B Snapshot & Restore Benchmark on vLLM (1×H100)

The benchmark compares initial cold start (pod 1 creating the snapshot on node 1) against fast horizontal scale-out (pod 2 restoring from the snapshot onto a second `a3-highgpu-1g` node, TP=1).

> [!NOTE]
> This guide's value metric is **pod startup latency**, so these figures were collected from Kubernetes pod
> lifecycle events and vLLM logs rather than request-throughput benchmarks
> ([`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) / `inference-perf`). Because snapshot restoration
> resumes the initialized vLLM process and its captured CUDA graphs in GPU memory, steady-state serving
> behavior is expected to match a standard deployment.

**Reference configuration:** `a3-highgpu-1g` (1× NVIDIA H100 80GB, driver `580.126.20`),
GKE `v1.36.4-gke.1082000`, gVisor sandbox, Spot provisioning (`--spot`), GKE Image Streaming enabled (`--enable-image-streaming`);
`Qwen/Qwen3-32B` on vLLM `v0.26.0` at `--gpu-memory-utilization 0.90` and `--max-model-len 8192` (CUDA graphs enabled).

## Comparing Cold Start to Snapshot Restore

Treat these as one measured data point, not a specification. Phase times measure from **Pod scheduled** (`PodScheduled=True`) through
**serving-ready** (for cold start: when model load, KV cache allocation, and CUDA graph capture complete right before `engine.sleep(level=1)`;
for snapshot restore: when Pod `Ready=True` and `/v1/models` returns HTTP `200`). Both include container startup, and both
exclude **node provisioning time** (`Pod created → Pod scheduled`, which varies by cloud capacity). Download speed, model size,
and GPU will move the figures.

| Metric | Without snapshots (Cold start) | Restore from snapshot |
| :--- | ---: | ---: |
| Pod scheduled → serving-ready | 4m 37s | **18.0s** |
| Weight loads from disk | Yes (61.03 GiB) | **No** |
| Speedup | — | **15.4×** |

<details>
<summary><b><i>Click</i></b> to view the per-phase breakdown</summary>

### Cold start — first pod, creates the snapshot

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | Excluded to isolate pod startup latency from cloud VM provisioning |
| Image pull | 4.0s | kubelet `Pulling` → `Pulled`; time to fetch the image onto the node before the container starts |
| **Pod scheduled → serving-ready** (weights loaded, KV cache allocated, CUDA graphs captured) | **4m 37s** | `PodScheduled=True` (kubelet `Scheduled` event) → `Executing engine.sleep(level=1)...`, measured directly as `277.4s`; the first `75.1s` is container start plus Python and CUDA imports, up to the vLLM log `Loading model from scratch...` |
| ↳ Model loading — download + load into VRAM | 129.8s | `Loading model from scratch...` → `Model loading took 61.03 GiB and 130.20 seconds`; `61.03 GiB` of weights, of which the safetensors read into VRAM is `43.41s` |
| ↳ Engine init (profile, KV cache, CUDA graph capture, warmup) | 72.4s | `Model loading took...` → `Executing engine.sleep(level=1)...`; CUDA graph capture is `9s` of this and costs `1.86 GiB` |
| `engine.sleep(level=1)` | 17.4s | `Executing engine.sleep(level=1)...` → `It took 17.412627 seconds to fall asleep`; offloads `61.68 GiB` of weights to host RAM and discards `8.52 GiB` of KV cache, leaving `2.90 GiB` resident |
| Checkpoint + upload to GCS | 33.2s | `It took 17.412627 seconds to fall asleep` → `PodSnapshot` `Ready=True`; snapshot is `65.73 GiB` across `componentCount: 2104` objects |
| **Total — Pod scheduled → snapshot `Ready`** (`PodSnapshot` condition `Ready=True`) | **5m 28s** | `277.4s + 17.4s + 33.2s = 328.0s`; excludes node provisioning and image pull |

### Restore — every subsequent pod

| Phase | Measured | Source / Notes |
| :--- | ---: | :--- |
| Node provisioning (`Pod created → Pod scheduled`) | varies (excluded) | GCE VM provisioning time (excluded to isolate pod restore latency) |
| Image pull | 4.0s | kubelet `Pulling` → `Pulled`; time to fetch the image onto the node before the container starts |
| Pod scheduled → process restored (rootfs mount, sandbox create, checkpoint stream from GCS) | 14.0s | `PodScheduled=True` → Pod condition `PodRestored=True`; container `startedAt` lands at `6.0s` into this window, so the remaining `8.0s` is GKE streaming the checkpoint from GCS and restoring the process image |
| Process restored → Pod `Ready` | 4.0s | `PodRestored=True` → Pod condition `Ready=True` |
| **Total — Pod scheduled → Pod `Ready`** (serving-ready: readiness probe `GET /v1/models` returns HTTP `200`) | **18.0s** | `PodScheduled=True` → `Ready=True` (`14.0s + 4.0s = 18.0s`); excludes node provisioning; a **15.4×** speedup versus the `4m 37s` cold start |

**Pod scheduled → process restored** measures from `PodScheduled=True` to the Pod condition `PodRestored=True`. The container's
`state.running.startedAt` falls `6.0s` in, once the kubelet has created and started the container and the gVisor sandbox is
running; GKE then streams the checkpoint from GCS and restores the process image (`Process restored from snapshot checkpoint`),
which accounts for the remaining `8.0s`.

**Process restored → Pod `Ready`** measures from `PodRestored=True` to the Pod condition `Ready=True`, i.e. the readiness probe
(`GET /v1/models` returning HTTP `200`) succeeds. It includes `engine.wake_up()` (`2.67s`), which copies weights from host RAM
back into VRAM, so a pod reporting `Ready` is serving requests, not merely restored.

</details>
