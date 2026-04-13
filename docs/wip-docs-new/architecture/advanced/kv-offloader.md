# KV-Cache Offloading

KV-Cache offloading extends the effective cache capacity beyond GPU HBM by moving KV blocks to lower-cost storage. llm-d supports two offloading backends: CPU RAM (via vLLM's native OffloadingConnector) and shared filesystem storage (via the llm-d FS backend). These currently operate as independent options—tiered offloading where blocks flow through multiple levels is under active development. [Other connectors](#other-connectors) like LMCache are also supported through vLLM/SGLang integration.

> KV-Cache offloading complements the [KV-Cache Indexer](./kv-indexer.md) which handles cache-aware routing. While the indexer determines *where* cached blocks exist, the offloader manages *how* blocks move between GPU memory and lower-cost tiers.

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

> **Future work:** Tiered offloading—where blocks flow GPU → CPU → Storage as a unified hierarchy—is under active development. Today, choose one offloading backend based on your workload requirements.

## Components

### vLLM Native CPU Offloading

vLLM's `OffloadingConnector` manages the GPU-to-CPU tier. It uses a hardware DMA engine for high-throughput transfers with minimal GPU core interference. The connector:

- Allocates pinned CPU memory for staging buffers
- Transfers KV blocks asynchronously using GPU DMA, avoiding interference with GPU compute cores.
- Uses a contiguous memory layout (introduced in vLLM 0.12.0) that groups all layers into single physical blocks, improving transfer throughput by 4-5x

CPU offloading requires no external infrastructure. Enable it with:

```bash
--kv_offloading_backend native --kv_offloading_size <size_in_GB>
```

### llm-d Filesystem Connector

The `llmd_fs_backend` is a storage backend that plugs into vLLM's OffloadingConnector. It stores KV blocks as files on a shared filesystem and loads them back on demand, using the filesystem directory as the index of cached blocks.

Key properties:

- **Filesystem agnostic** — Relies on standard POSIX file operations, works with any filesystem (CephFS, Lustre, IBM Storage Scale, local NVMe)
- **KV sharing across instances and nodes** — Multiple vLLM servers reuse cached prefixes by accessing the same shared path
- **Persistence across restarts** — KV data survives pod restarts, rescheduling, and node failures
- **Fully asynchronous I/O** — Reads and writes run without blocking the inference path
- **High throughput via parallelism** — I/O operations parallelized across worker threads with NUMA-aware scheduling
- **Minimal GPU interference** — Uses GPU DMA by default, reducing interference with compute kernels

> **Note:** The storage connector does not handle cleanup or eviction. Storage capacity management must be handled by the underlying storage system or an external controller. A reference implementation, the [PVC Evictor](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/pvc_evictor), can automatically clean up old KV-cache files when storage thresholds are exceeded.

For implementation details and advanced configuration, see the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

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

For the full configuration reference including GDS modes and environment variables, see the [llm-d FS backend README](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend).

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

## Metrics

The FS backend populates vLLM's built-in offloading metrics (`vllm:kv_offload_*`) for transfer bytes, time, and size distribution. See the [llm-d FS backend documentation](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend#metrics) for the full metrics reference.

## Performance Considerations

**CPU offloading:** Should always be enabled if CPU DRAM is larger than GPU HBM space. It has minimal overhead when the cache fits in HBM, and provides significant benefits when it doesn't—recovering preempted requests without recomputation and extending effective cache capacity with low latency.

**Storage offloading:** Best when cache working set exceeds single-node capacity, when cross-node sharing is valuable (repeated system prompts across replicas, agentic workflows), or when persistence across restarts matters. Storage offloading is most effective when the storage network is fast enough to allow low-latency loads and stores.

**Storage selection:** The FS backend works with any POSIX filesystem. Performance varies by backend:
- Local NVMe: Lowest latency, no sharing
- CephFS: Good balance of performance and sharing
- IBM Storage Scale, GCP Lustre: High throughput for large deployments

**Block size tuning:** Larger `block_size` values (256-512 tokens) improve I/O efficiency but require longer matching prefixes for a cache hit. Match to your typical prefix lengths.

## Further Reading

- [llm-d FS Backend](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend) — Implementation details, configuration, and metrics
- [Tiered Prefix Cache Guide](https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache) — Step-by-step deployment guides
- [vLLM KV Offloading Connector](https://vllm.ai/blog/kv-offloading-connector) — Deep dive into vLLM's native offloading
- [Native KV Cache Offloading Blog](https://llm-d.ai/blog/native-kv-cache-offloading-to-any-file-system-with-llm-d) — Benchmarks and design rationale