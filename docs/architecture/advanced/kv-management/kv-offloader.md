# KV-Cache Offloading

KV-Cache offloading extends the effective cache capacity beyond GPU HBM by moving KV blocks to lower-cost tiers like CPU DRAM and shared storage.

llm-d works with any KV-cache connector compatible with vLLM or SGLang. Two integration patterns are supported:

- **Native (vLLM `OffloadingConnector`)** — vLLM's built-in offloading path. Targets CPU RAM directly, and a shared filesystem via the [llm-d FS backend](https://github.com/llm-d/llm-d-kv-cache).
- **Out-of-tree connectors** — third-party cache engines (e.g., [LMCache](https://lmcache.ai), [Mooncake](https://github.com/kvcache-ai/Mooncake), [NVIDIA KVBM](https://docs.nvidia.com/dynamo/latest/kvbm/)) that plug into the model server through its KV-cache connector API and own their own indexing, memory management, and storage.

> [!NOTE]
> Pairs with the **llm-d Router's** cache-aware routing — the Router (specifically the **EPP**) picks replicas that can reuse cached blocks; offloading grows the cache each replica can hold.

## Functionality

Transformer inference computes Key and Value tensors during prefill, then reuses them during decode. For long contexts or repeated prefixes (system prompts, agentic loops, multi-turn conversations), recomputing these tensors wastes significant GPU cycles. KV-Cache offloading addresses two scaling limitations:

1. **Capacity** — GPU HBM is limited (tens of GB per GPU). CPU RAM adds another order of magnitude, but storage can scale nearly infinitely.

2. **Sharing** — Local caches are isolated per model-server instance. Shared storage enables cross-node cache reuse, faster scale-up for new replicas, and persistence across pod restarts.

The offloading system generally operates asynchronously. Writes to lower tiers happen in the background without blocking inference. Reads from storage still require waiting, but loading cached blocks is typically faster than recomputing them—up to 16x faster for long prompts.

## Architecture

The two integration patterns map to distinct architectures.

### Native (vLLM OffloadingConnector)

The native path lives entirely inside the vLLM stack. The `OffloadingConnector` dispatches blocks to either the CPU tier or the shared-storage tier:

```
┌─────────────────────────────────────────────────────────────────┐
│                            vLLM                                 │
├─────────────────────────────────────────────────────────────────┤
│                      V1 Connector API                           │
├─────────────────────────────────────────────────────────────────┤
│                    Offloading Connector                         │
│        ┌───────────┐   ┌───────────┐   ┌───────────┐            │
│        │ Scheduler │   │  Worker   │   │  Metrics  │            │
│        └───────────┘   └───────────┘   └───────────┘            │
├─────────────────────────────────────────────────────────────────┤
│                       Offloading API                            │
├───────────────────────────────┬─────────────────────────────────┤
│             CPU               │            Storage              │
│  ┌─────────┐   ┌──────────┐   │  ┌─────────┐   ┌─────────────┐  │
│  │ Manager │   │  Worker  │   │  │ Manager │   │   Worker    │  │
│  │         │   │          │   │  │         │   │             │  │
│  │ ┌─────┐ │   │┌────────┐│   │  │┌───────┐│   │┌───────────┐│  │
│  │ │ LRU │ │   ││Transfer││   │  ││Lookup ││   ││Thread-Pool││  │
│  │ └─────┘ │   ││GPU-CPU ││   │  │└───────┘│   │├───────────┤│  │
│  │         │   │└────────┘│   │  │         │   ││Transfer   ││  │
│  │         │   │          │   │  │         │   ││GPU-CPU    ││  │
│  │         │   │          │   │  │         │   │├───────────┤│  │
│  │         │   │          │   │  │         │   ││POSIX API  ││  │
│  │         │   │          │   │  │         │   │└───────────┘│  │
│  └─────────┘   └──────────┘   │  └─────────┘   └─────────────┘  │
└───────────────────────────────┴─────────────────────────────────┘
```

| Target | Latency | Capacity | Scope | Best For |
| :--- | :--- | :--- | :--- | :--- |
| CPU RAM | Low | ~250GB/GPU | Per-node | High-frequency reuse, preemption recovery |
| Shared Storage | Higher | TB+ | Cross-cluster | Cross-node sharing, persistence, massive scale |

Today, the two targets operate as independent options — choose one offloading target based on your workload requirements.

> [!NOTE]
> **Hierarchical KV-cache offloading** — where blocks flow GPU → CPU → Storage as a unified tiered hierarchy — is under active development in the native path.

### Out-of-tree Connectors

Third-party connectors (see [Other Connectors](#other-connectors)) adapt an external KV-cache engine to the model server through its KV-cache connector API. The same pattern is present across major serving stacks — vLLM's V1 Connector API, SGLang's HiCache, and TensorRT-LLM's KV Cache Connector API. Unlike the native path, the cache logic — indexing, memory management, tiering, eviction, and remote storage — lives in a separate engine, often a distinct process or service:

```
┌─────────────────────────────────────────────────────────────────┐
│               Model Server (vLLM / SGLang / TRT-LLM)            │
├─────────────────────────────────────────────────────────────────┤
│                     KV-Cache Connector API                      │
├─────────────────────────────────────────────────────────────────┤
│              Third-Party Connector (adapter)                    │
│      bridges lookup / store / load calls to the engine          │
└───────────────────────────────┬─────────────────────────────────┘
                                │  IPC / shared memory / RPC
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   External KV-Cache Engine                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Cache Index  │  │ Memory Mgr   │  │ Controller   │           │
│  │ (token → KV) │  │ (pinned pool)│  │ (mgmt, evict)│           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│  ┌───────────────────────────────────────────────────┐          │
│  │         Async Offload & Transfer Workers          │          │
│  └───────────────────────────────────────────────────┘          │
├────────────┬──────────────────┬─────────────────────────────────┤
│    CPU     │   Local Disk     │        Remote Backends          │
│   DRAM     │  (NVMe, etc.)    │   (object store, KV store, ...) │
└────────────┴──────────────────┴─────────────────────────────────┘
```

This pattern trades deployment complexity for flexibility: the external engine is independently versioned, can coordinate across multiple inference replicas, and typically supports a wider range of storage backends.

## Components

### vLLM Native CPU Offloading

vLLM's `OffloadingConnector` manages the GPU-to-CPU tier. It uses a hardware DMA engine for high-throughput transfers with minimal GPU core interference. The connector:

- Allocates pinned CPU memory for staging buffers
- Transfers KV blocks asynchronously using GPU DMA, avoiding interference with GPU compute cores.
- Uses a contiguous memory layout (introduced in vLLM 0.12.0) that groups all layers into single physical blocks, improving transfer throughput by 4-5x

CPU offloading requires no external infrastructure. The simplest way to enable it is via vLLM's dedicated top-level flags:

```bash
--kv-offloading-backend native --kv-offloading-size <size_in_GB>
```

Equivalent to passing `--kv-transfer-config '{"kv_connector":"OffloadingConnector","kv_role":"kv_both",...}'` directly — the top-level flags are a convenience wrapper around the connector JSON.

### llm-d Filesystem Connector

The `llmd_fs_backend` is a storage backend that plugs into vLLM's OffloadingConnector. It stores KV blocks as files on a shared filesystem and loads them back on demand, using the filesystem directory as the index of cached blocks.

Key properties:

- **Filesystem agnostic** — Relies on standard POSIX file operations, works with any filesystem (CephFS, Lustre, IBM Storage Scale, local NVMe)
- **KV sharing across instances and nodes** — Multiple vLLM servers reuse cached prefixes by accessing the same shared path
- **Persistence across restarts** — KV data survives pod restarts, rescheduling, and node failures
- **Fully asynchronous I/O** — Reads and writes run without blocking the inference path
- **High throughput via parallelism** — I/O operations parallelized across worker threads with NUMA-aware scheduling
- **Minimal GPU interference** — Uses GPU DMA by default, reducing interference with compute kernels

> [!NOTE]
> The storage connector does not handle cleanup or eviction. Storage capacity management must be handled by the underlying storage system or an external controller. A reference implementation, the [PVC Evictor](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/pvc_evictor), can automatically clean up old KV-cache files when storage thresholds are exceeded.

For implementation details and advanced configuration, see the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

### Other Connectors

Out-of-tree engines coexist with the native path through a common integration contract on the llm-d side:

- **Serving-stack side** — each engine is already connector-compatible with one or more of vLLM, SGLang (via HiCache), and TensorRT-LLM, so the model server drives lookups, stores, and loads through its standard KV-cache connector API.
- **Scheduling side** — connectors integrate with llm-d through **KV-Events**: cache mutation notifications that the [KV-Cache Indexer](./kv-indexer.md) consumes to maintain a global view of cache distribution, enabling prefix-aware routing regardless of which backend is in use.

> [!NOTE]
> llm-d's deployment guides formally cover LMCache today. The integration pattern is the same for Mooncake, KVBM, and other connector-compatible engines — they work out-of-the-box on the serving-stack side — but first-class llm-d recipes for each are not yet in the repo.

For existing deployment recipes, see the [Tiered Prefix Cache Guide](https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache).

## Configuration

### CPU Offloading (vLLM Native)

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--kv-offloading-backend` | string | - | Set to `native` for vLLM's built-in CPU offloading |
| `--kv-offloading-size` | integer | - | CPU offloading buffer size in GB (per vLLM instance, across all workers) |

For advanced use and older vLLM releases, the equivalent `--kv-transfer-config` JSON form is supported. See the [vLLM offloading connector blog](https://vllm.ai/blog/kv-offloading-connector) for details.

### Storage Offloading (llm-d FS Backend)

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `shared_storage_path` | string | `/tmp/shared-kv` | Base path for KV-cache files |
| `block_size` | integer | `256` | Tokens per file (must be multiple of GPU block size) |
| `threads_per_gpu` | integer | `64` | I/O worker threads per GPU |

For the full configuration reference including GDS modes and environment variables, see the [llm-d FS backend README](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

## Examples

### CPU Offloading with vLLM

```yaml
args:
  - "--model=Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--kv-offloading-backend=native"
  - "--kv-offloading-size=100"
```

### Storage Offloading with llm-d FS Backend

```yaml
args:
  - "--model=Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--block-size=16"
  - "--distributed-executor-backend=mp"
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

## Metrics

The FS backend populates vLLM's built-in offloading metrics (`vllm:kv_offload_*`) for transfer bytes, time, and size distribution. See the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend#metrics) for the full metrics reference.

## Performance Considerations

**CPU offloading:** Should always be enabled if CPU DRAM is larger than GPU HBM space. It has minimal overhead when the cache fits in HBM, and provides significant benefits when it doesn't—recovering preempted requests without recomputation and extending effective cache capacity with low latency.

**Storage offloading:** Best when cache working set exceeds single-node capacity, when cross-node sharing is valuable (repeated system prompts across replicas, agentic workflows), or when persistence across restarts matters. Storage offloading is most effective when the storage network is fast enough to allow low-latency loads and stores.

**Storage selection:** The FS backend works with any POSIX-compliant filesystem and is not tied to a specific vendor. Your choice trades off:
- **Sharing scope** — local media (e.g., NVMe SSDs) are per-node only; networked or distributed filesystems (e.g., NFS, CephFS, Lustre, IBM Storage Scale, Weka, cloud file services) enable cross-node reuse.
- **Throughput and latency** — depend on the underlying hardware, network, and filesystem configuration rather than the KV-cache layer; parallel and distributed filesystems generally scale best for large deployments.

Any POSIX filesystem is a candidate; the best choice for a given deployment depends on existing storage infrastructure, required scale, and latency targets.

**Block size tuning:** Larger `block_size` values (256-512 tokens) improve I/O efficiency but require longer matching prefixes for a cache hit. Match to your typical prefix lengths.

## Further Reading

- [Tiered Prefix Cache Guide](https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache) — Step-by-step deployment guides
- [llm-d KV-Disaggregation Roadmaps](https://github.com/llm-d/llm-d-kv-cache/issues?q=is%3Aissue%20state%3Aopen%20label%3Aroadmap) — Planned features and improvements across offloading and KV-cache management
- [llm-d FS Backend](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend) — Implementation details, configuration, and metrics
- [vLLM KV Offloading Connector](https://vllm.ai/blog/kv-offloading-connector) — Deep dive into vLLM's native offloading
