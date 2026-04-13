# KV-Cache Offloading

KV-Cache offloading extends the effective cache capacity beyond GPU HBM by moving KV blocks to lower-cost storage. llm-d supports two offloading backends: CPU RAM (via vLLM's native OffloadingConnector) and shared filesystem storage (via the llm-d FS backend). These currently operate as independent options—tiered offloading where blocks flow through multiple levels is under active development. [Other connectors](#other-connectors) like LMCache are also supported through vLLM/SGLang integration.

> KV-Cache offloading complements the [KV-Cache Indexer](./kv-indexer.md) which handles cache-aware routing. While the indexer determines *where* cached blocks exist, the offloader manages *how* blocks move between GPU memory and offload targets.

## Functionality

Transformer inference computes Key and Value tensors during prefill, then reuses them during decode. For long contexts or repeated prefixes (system prompts, agentic loops, multi-turn conversations), recomputing these tensors wastes significant GPU cycles. KV-Cache offloading addresses two scaling limitations:

1. **Capacity** — GPU HBM is limited (tens of GB per GPU). CPU RAM adds another order of magnitude, but storage can scale nearly infinitely.

2. **Sharing** — Local caches are isolated per vLLM instance. Shared storage enables cross-node cache reuse, faster scale-up for new replicas, and persistence across pod restarts.

The offloading system operates asynchronously. Writes to lower tiers happen in the background without blocking inference. Reads from storage still require waiting, but loading cached blocks is typically faster than recomputing them—up to 16x faster for long prompts.

## Offloading Architecture

llm-d supports two offloading targets. Each extends cache capacity beyond GPU HBM with different tradeoffs:

```
                                    ┌─────────────────────────────────────┐
                                    │            vLLM Engine              │
                                    │           (GPU HBM Cache)           │
                                    └──────────────────┬──────────────────┘
                                                       │
                          ┌────────────────────────────┴────────────────────────────┐
                          │                                                         │
                          ▼                                                         ▼
               ┌─────────────────────┐                               ┌─────────────────────┐
               │      CPU RAM        │                               │   Shared Storage    │
               │                     │                               │                     │
               │  vLLM Native        │                               │  llm-d FS Backend   │
               │  OffloadingConnector│                               │                     │
               │                     │                               │  ┌───────────────┐  │
               │  • No extra infra   │                               │  │  PVC Evictor  │  │
               │  • ~250GB/GPU       │                               │  │  (cleanup)    │  │
               │  • Per-node scope   │                               │  └───────────────┘  │
               └─────────────────────┘                               │                     │
                                                                     │  • Cross-node       │
                                                                     │  • TB+ capacity     │
                                                                     │  • Persistent       │
                                                                     └─────────────────────┘
```

| Target | Latency | Capacity | Scope | Best For |
| :--- | :--- | :--- | :--- | :--- |
| CPU RAM | Low | ~250GB/GPU | Per-node | High-frequency reuse, preemption recovery |
| Shared Storage | Higher | TB+ | Cross-cluster | Cross-node sharing, persistence, massive scale |

> **Future work:** Tiered offloading—where blocks flow GPU → CPU → Storage as a unified hierarchy—is under active development. Today, choose one offloading backend based on your workload requirements, or combine them using the vLLM MultiConnector.

## Components

### vLLM Native CPU Offloading

vLLM's `OffloadingConnector` manages the GPU-to-CPU tier. It uses a hardware DMA engine for high-throughput transfers with minimal GPU core interference. The connector:

- Allocates pinned CPU memory for staging buffers
- Transfers KV blocks asynchronously using `cudaMemcpyAsync`
- Uses a contiguous memory layout (introduced in vLLM 0.12.0) that groups all layers into single physical blocks, improving transfer throughput by 4-5x

CPU offloading requires no external infrastructure. Enable it with:

```bash
--kv_offloading_backend native --kv_offloading_size <size_in_GB>
```

### llm-d Filesystem Connector

The `llmd_fs_backend` extends vLLM's offloading pipeline to write KV blocks to shared POSIX storage. It implements vLLM's `OffloadingSpec`, `OffloadingManager`, and `OffloadingHandler` interfaces.

#### Design Principles

**Stateless Manager** — The filesystem directory serves as the index. To check if a block exists, the manager calls `os.path.exists()` on the computed file path. No separate metadata store is required.

**Content-Addressed Files** — Block hashes determine file paths using a two-level directory structure that limits fan-out:

```
<root>/<model>/<config>/<rank>/<dtype>/<hhh>/<hh>/<hash>.bin

Example:
/mnt/kv-cache/Qwen3-32B/block_size_16_blocks_per_file_16/tp_2_pp_size_1_pcp_size_1/rank_0/bfloat16/a3f/42/a3f42e8b1c9d7a6f.bin
```

**Atomic Writes** — The C++ engine writes to a temp file then renames, preventing partial reads.

**Read-Preferring Priority** — Worker threads prioritize reads over writes (default 75% read bias) since reads are latency-sensitive while writes can be deferred.

#### Data Path

The connector uses a high-performance C++ engine (`storage_offload.so`) with:

- Thread-local pinned staging buffers for GPU↔CPU transfers
- NUMA-aware thread pool for parallel I/O
- GPU DMA by default, with optional GPU-kernel-based copying via `USE_KERNEL_COPY_READ`/`USE_KERNEL_COPY_WRITE`
- Optional GPUDirect Storage (GDS) for direct GPU↔disk transfers

```
GPU Block ──▶ [DMA] ──▶ Pinned CPU Buffer ──▶ [I/O Thread] ──▶ Filesystem
              async        per-thread              parallel
```

### PVC Evictor

The `pvc_evictor` is a Kubernetes-native cleanup daemon that prevents storage exhaustion. It runs as a sidecar or standalone deployment, monitoring PVC usage and deleting old cache files when thresholds are exceeded.

#### Process Architecture

The evictor uses an N+2 multiprocess design:

```
┌─────────────────────────────────────────────────────────────────┐
│                        Main Process                             │
│                    (spawns, monitors, logs)                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
       ┌───────────────────────┼───────────────────────────────┐
       │                       │                               │
       ▼                       ▼                               ▼
┌─────────────┐         ┌─────────────┐                 ┌─────────────┐
│  Crawler 1  │   ...   │  Crawler N  │                 │  Activator  │
│  (hex 0-1)  │         │  (hex E-F)  │                 │  (statvfs)  │
└──────┬──────┘         └──────┬──────┘                 └──────┬──────┘
       │                       │                               │
       └───────────┬───────────┘                               │
                   │                                           │
                   ▼                                           ▼
          ┌───────────────┐                           ┌───────────────┐
          │ Deletion Queue │◀──────────────────────────│deletion_event │
          │    (FIFO)     │                           │    (flag)     │
          └───────┬───────┘                           └───────────────┘
                  │
                  ▼
          ┌─────────────┐
          │   Deleter   │
          │ (xargs rm)  │
          └─────────────┘
```

| Process | Role |
| :--- | :--- |
| Crawlers (1-N) | Discover files using hash-based load balancing, filter by access time, queue for deletion |
| Activator | Monitor disk usage via `statvfs()`, set deletion flag when usage ≥ 85% |
| Deleter | Batch-delete files using `xargs rm -f` when flag is set, stop when usage ≤ 70% |

**Access-Time Filtering** — Crawlers skip files accessed within a configurable threshold (default 60 minutes), protecting hot cache entries from eviction.

**Hysteresis** — Two thresholds prevent rapid on/off cycling: cleanup triggers at 85% usage and stops at 70%.

### Other Connectors

llm-d works with any KV-cache connector compatible with vLLM or SGLang. Beyond the native and filesystem backends described above, [LMCache](https://lmcache.ai) provides an alternative with support for multiple storage backends and its own caching strategies.

All connectors integrate with llm-d's scheduling layer through **KV-Events**—cache mutation notifications that the [KV-Cache Indexer](./kv-indexer.md) consumes to maintain a global view of cache distribution. This enables prefix-aware routing regardless of which offloading backend is in use.

For deployment guides covering LMCache and other connector options, see the [Tiered Prefix Cache Guide](https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache).

## Configuration

### CPU Offloading (vLLM Native)

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kv_offloading_backend` | string | - | Set to `native` to enable CPU offloading |
| `kv_offloading_size` | integer | - | CPU cache size in GB |

Or via `--kv-transfer-config`:

```json
{
  "kv_connector": "OffloadingConnector",
  "kv_role": "kv_both",
  "kv_connector_extra_config": {
    "num_cpu_blocks": 10000
  }
}
```

### Storage Offloading (llm-d FS Backend)

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `shared_storage_path` | string | `/tmp/shared-kv` | Base path for KV-cache files |
| `block_size` | integer | `256` | Tokens per file (must be multiple of GPU block size) |
| `threads_per_gpu` | integer | `64` | I/O worker threads per GPU |
| `max_staging_memory_gb` | integer | `150` | Total staging buffer memory limit |
| `gds_mode` | string | `disabled` | GPUDirect Storage mode |
| `read_preferring_ratio` | float | `0.75` | Fraction of workers prioritizing reads |

**GDS Modes:** `disabled`, `read_only`, `write_only`, `read_write`, `bb_read_only`, `bb_write_only`, `bb_read_write`

**Environment Variables:**

| Variable | Description |
| :--- | :--- |
| `STORAGE_LOG_LEVEL` | Log level: `trace`, `debug`, `info`, `warn`, `error` |
| `USE_KERNEL_COPY_READ` | Use GPU SM-based reads instead of DMA (0/1) |
| `USE_KERNEL_COPY_WRITE` | Use GPU SM-based writes instead of DMA (0/1) |

### PVC Evictor

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `CACHE_DIR` | string | `/kv-cache` | Path to monitor |
| `CLEANUP_THRESHOLD` | float | `0.85` | Disk usage fraction to trigger deletion |
| `TARGET_THRESHOLD` | float | `0.70` | Disk usage fraction to stop deletion |
| `ACCESS_TIME_THRESHOLD_MINUTES` | integer | `60` | Skip files accessed within this window |
| `NUM_CRAWLERS` | integer | `8` | Number of crawler processes (1, 2, 4, 8, or 16) |
| `BATCH_SIZE` | integer | `100` | Files per deletion batch |

## Examples

### CPU Offloading with vLLM

```yaml
args:
  - "--model=Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--block-size=16"
  - "--kv_offloading_backend=native"
  - "--kv_offloading_size=100"
```

### Storage Offloading with llm-d FS Backend

```yaml
args:
  - "--model=Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--block-size=16"
  - "--distributed_executor_backend=mp"
  - "--kv-transfer-config"
  - |
    {
      "kv_connector": "OffloadingConnector",
      "kv_role": "kv_both",
      "kv_connector_extra_config": {
        "spec_name": "SharedStorageOffloadingSpec",
        "spec_module_path": "llmd_fs_backend.spec",
        "shared_storage_path": "/mnt/kv-cache/",
        "block_size": 256,
        "threads_per_gpu": 64
      }
    }
volumeMounts:
  - name: kv-cache
    mountPath: /mnt/kv-cache
```

### Tiered Offloading (CPU + Storage)

> **Coming soon:** Combined CPU + Storage tiering is under development. Track progress at [llm-d/llm-d#682](https://github.com/llm-d/llm-d/issues/682).

### PVC Evictor Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kv-cache-evictor
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: evictor
          image: ghcr.io/llm-d/pvc-evictor:latest
          env:
            - name: CACHE_DIR
              value: "/kv-cache"
            - name: CLEANUP_THRESHOLD
              value: "0.85"
            - name: TARGET_THRESHOLD
              value: "0.70"
          volumeMounts:
            - name: kv-cache
              mountPath: /kv-cache
      volumes:
        - name: kv-cache
          persistentVolumeClaim:
            claimName: kv-cache-pvc
```

## Metrics

The FS backend populates vLLM's built-in offloading metrics:

| Metric | Type | Description |
| :--- | :--- | :--- |
| `vllm:kv_offload_total_bytes` | Counter | Total bytes transferred, labeled by `transfer_type` |
| `vllm:kv_offload_total_time` | Counter | Total transfer time in seconds, labeled by `transfer_type` |
| `vllm:kv_offload_size` | Histogram | Distribution of transfer sizes in bytes |

**Labels:**
- `GPU_to_SHARED_STORAGE` — writes to storage
- `SHARED_STORAGE_to_GPU` — reads from storage

## Performance Considerations

**When to use CPU offloading:** Best for workloads with high cache reuse that fit within node-local memory. Low operational overhead, no external dependencies, and the lowest offload latency. Also helps recover preempted requests without recomputation.

**When to use storage offloading:** Best when cache working set exceeds single-node capacity, when cross-node sharing is valuable (repeated system prompts across replicas, agentic workflows), or when persistence across restarts matters.

**Storage selection:** The FS backend works with any POSIX filesystem. Performance varies by backend:
- Local NVMe: Lowest latency, no sharing
- CephFS: Good balance of performance and sharing
- IBM Storage Scale, GCP Lustre: High throughput for large deployments

**Block size tuning:** Larger `block_size` values (256-512 tokens) improve I/O efficiency but increase minimum cache granularity. Match to your typical prefix lengths.

## Further Reading

- [llm-d-kv-cache: FS Backend](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend) — Implementation details and advanced configuration
- [llm-d-kv-cache: PVC Evictor](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/pvc_evictor) — Evictor architecture and deployment
- [Tiered Prefix Cache Guide](https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache) — Step-by-step deployment guides
- [vLLM KV Offloading Connector](https://vllm.ai/blog/kv-offloading-connector) — Deep dive into vLLM's native offloading
- [Native KV Cache Offloading Blog](https://llm-d.ai/blog/native-kv-cache-offloading-to-any-file-system-with-llm-d) — Benchmarks and design rationale