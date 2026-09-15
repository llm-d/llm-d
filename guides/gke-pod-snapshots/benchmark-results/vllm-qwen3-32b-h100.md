# Qwen/Qwen3-32B Snapshot & Restore Benchmark on vLLM (1×H100)

The benchmark runs on a single H100 80GB GPU under gVisor, with one model server pod (TP=1).

> [!NOTE]
> This guide's value metric is **pod startup latency**, not request throughput, so these figures were
> collected from pod lifecycle events and model server logs rather than from
> [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) / `inference-perf`. Steady-state serving
> performance after a restore is unchanged from a normal vLLM deployment — the snapshot restores the
> same process, not a degraded one.

**Reference configuration:** `a3-highgpu-1g` (1× NVIDIA H100 80GB HBM3, driver `580.126.20`),
GKE `v1.36.4-gke.1082000`, gVisor sandbox, Spot provisioning, 300 GB boot disk;
`Qwen/Qwen3-32B` on vLLM `v0.26.0` at `--gpu-memory-utilization 0.95` and `--enforce-eager`.

> [!NOTE]
> The servers run with `--gpu-memory-utilization=0.95` because the default `0.90` leaves too little KV
> headroom for Qwen3-32B's 40960-token context on an 80&nbsp;GB H100 — at `0.90` the engine cannot start
> at all. If you swap models, check the `Available KV cache memory` line during startup before
> assuming the shipped value fits.

The single-GPU A3 shapes (`a3-highgpu-1g`, `-2g`, `-4g`) are only offered as Spot or Flex-start VMs —
see [GPU machine types](https://cloud.google.com/compute/docs/gpus). That is a property of those
machine types, not a requirement of this guide, and it needed no change to the manifests: GKE injects
the `sandbox.gke.io/runtime` and `nvidia.com/gpu` tolerations automatically, and the node pool carries
no Spot taint.

## Comparing Cold Start to Snapshot Restore

Treat these as one measured data point, not a specification. Phase times are measured from
**container start** (the first vLLM log line), excluding image pull, scheduling, and node
provisioning, so they are comparable across runs on warm and cold nodes and isolate the snapshot's
own contribution. Download speed, model size, and GPU will move the figures.

| Metric | Without snapshots | Restore from snapshot |
| :------------------------------- | ----------------: | --------------------: |
| Container start → serving engine  | 2m 56s            | **26.0s**             |
| Weight loads from disk     | 1                 | **0**                 |
| Speedup                          | —                 | **~6.8×**             |

> [!IMPORTANT]
> The first pod additionally pays a **one-time 16m 41s** to create the snapshot, most of it the frozen
> upload to GCS. The comparison above is what every *subsequent* pod sees, which is the case this guide
> optimizes. A deployment that never replaces a pod will not recover that cost.

<details>
<summary><b><i>Click</i></b> to view the per-phase breakdown</summary>

### Cold start — first pod, creates the snapshot

| Phase | Measured |
| :--- | ---: |
| Image pull (8.91 GB) | 69s |
| Model loading — download + load into VRAM (61.03 GiB) | 123s |
| &nbsp;&nbsp;&nbsp;&nbsp;↳ of which, safetensors read into VRAM | 41s |
| Engine init (profile, KV cache, warmup) | 19.9s |
| **Container start → checkpoint triggered** | **3m 16s** |
| `engine.sleep(level=1)` freed | 76.43 GiB (0.94 GiB still in use) |
| Time to fall asleep | 16.4s |
| Checkpoint + upload to GCS | **13m 52s** |
| **Total to snapshot `Ready`** | **16m 41s** |
| Snapshot size in GCS | **64.73 GiB** |

The checkpoint upload dominates: roughly 83% of the cold start is spent writing pages to GCS, during which
the pod is frozen and emits no logs.

### Restore — every subsequent pod

| Phase | Measured |
| :--- | ---: |
| Pod created → scheduled | 2s |
| Scheduled → container started | 4s |
| **Container started → pod `Ready`** | **26.0s** |
| `engine.wake_up()` | 2.68s |
| Safetensors loads performed | **none** |

The 6s before the container starts is scheduling and sandbox setup on a warm node with the image
already cached. It is excluded from the headline figure deliberately: in a scale-out that interval
is node provisioning, which varies by orders of magnitude and would otherwise mask the snapshot's
actual contribution.

### Throughput of the snapshot path

| Direction | Rate |
| :--- | ---: |
| Checkpoint write to GCS | 83.5 MB/s |
| Restore read from GCS | ~2 GB/s |

Upload ran at 83.5 MB/s here, 82.9 MB/s on an earlier vLLM v0.25.0 run, and 82.0 MB/s on a smaller
A100 / 8B run — within 2% across different GPUs, two vLLM releases, and a 3.5× difference in snapshot
size, which suggests a fixed ceiling of the snapshot write path rather than a property of the
workload.

As a planning rule, **snapshot GB ÷ 5 ≈ minutes of checkpoint freeze**.

</details>
