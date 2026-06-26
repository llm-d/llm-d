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

### MooncakeStoreConnector

[Mooncake Store](https://github.com/kvcache-ai/Mooncake) is a distributed KV cache system built on the Mooncake Transfer Engine. A centralized Mooncake Master manages object metadata, replica placement, leases, and eviction, while the Transfer Engine moves the underlying bytes between registered memory regions using RDMA or TCP. Together, they pool CPU DRAM across nodes—and optionally SSD/NVMe—into a shared cache tier without requiring a shared filesystem or PVC for the cache data path.

The `MooncakeStoreConnector` integrates this store with vLLM's V1 Connector API. It converts vLLM's content-addressed KV cache chunks into Mooncake object keys and maps their tensor data to the byte ranges stored and transferred by Mooncake. This allows multiple vLLM instances to retrieve and reuse cached prefixes, reducing redundant prefill computation.

  - At the vLLM boundary, cache data is identified as content-addressed KV-cache blocks or chunks derived from vLLM block hashes.
  - At the Mooncake boundary, each content-addressed key identifies a variable-length byte object. The connector maps that object to one or more registered memory ranges containing the corresponding K/V tensor data.

> [!NOTE]
> `MooncakeStoreConnector` (distributed cache offloading) is distinct from `MooncakeConnector` (point-to-point KV transfer for P/D disaggregation). They share the same Transfer Engine for RDMA data movement but serve different purposes and are configured independently. They can be composed via vLLM's `MultiConnector` when both P/D disaggregation and shared cache offloading are needed.

#### Architecture

The system consists of four components:

- **Mooncake Master** — Centralized metadata service managing keyed objects, their replicas and placement, leases, and eviction. It is unaware of vLLM's KV-cache block semantics. Deployment manifests are provided in [`helpers/mooncake-master-store/`](https://github.com/llm-d/llm-d/tree/main/helpers/mooncake-master-store). Required by both deployment modes.
- **Mooncake Client** — A standalone process that allocates CPU DRAM and optionally SSD storage, registers those resources with the Master, and serves RDMA read/write requests from vLLM ranks. Only used in standalone-store mode. Deployment manifests are provided in [`helpers/mooncake-client/`](https://github.com/llm-d/llm-d/tree/main/helpers/mooncake-client).
- **Mooncake Transfer Engine** — Byte-oriented data mover that transfers data between registered GPU or CPU memory regions and the distributed DRAM/SSD pool using RDMA or TCP.
- **MooncakeStoreConnector** (in each vLLM process) — Converts vLLM content-addressed cache chunks into Mooncake object keys and maps each object to one or more physical memory address-and-size ranges.

Two deployment modes are available:

| Mode | Description | `global_segment_size` | Components |
| :--- | :--- | :--- | :--- |
| **Embedded** | Each vLLM rank contributes CPU DRAM to the distributed pool in-process | > 0 (e.g., `"80GB"`) | Master + vLLM |
| **Standalone-store** | External Mooncake Client process owns the CPU + SSD pool; vLLM ranks are pure requesters | `0` | Master + Client + vLLM |

Embedded mode is simpler to deploy — the DRAM pool scales automatically with the number of vLLM instances. Standalone-store mode decouples storage from compute and enables the SSD/NVMe tier by delegating resource ownership to the Mooncake Client. An example of when you might need the standalone mode as compared to the embedded mode, would be if the CPU being contributed to the distributed pool does not belong to the vLLM servers, and so you need some other process to manage the physical memory and disk.

#### Mooncake Master

The Mooncake Master handles block metadata, eviction, and snapshots. Key configuration parameters (set in [`configmap.yaml`](https://github.com/llm-d/llm-d/blob/main/helpers/mooncake-master-store/base/configmap.yaml)):

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `eviction_high_watermark_ratio` | `0.95` | Trigger eviction when pool is 95% full |
| `eviction_ratio` | `0.05` | Evict 5% of pool capacity per cycle |
| `default_kv_lease_ttl` | `5000` | Lease TTL in ms |
| `default_kv_soft_pin_ttl` | `1800000` | Soft pin TTL in ms (30 min) |
| `enable_snapshot` | `true` | Periodic snapshots to PVC for recovery |
| `snapshot_interval_seconds` | `60` | Snapshot frequency |

The Master exposes gRPC (port 50051), HTTP metadata (port 8080), and Prometheus metrics (port 9003). See [`helpers/mooncake-master-store/`](https://github.com/llm-d/llm-d/tree/main/helpers/mooncake-master-store) for deployment manifests.

#### Mooncake Client

The Mooncake Client (`mooncake_client`) is the process that owns physical memory and disk in the distributed pool. It is only used in standalone-store mode.

On startup, the Client allocates a CPU DRAM segment (`--global_segment_size`) and optionally opens a local SSD path for persistence (`--enable_offload=true`). It registers these resources with the Mooncake Master, making them available to the cluster. These allocations are fixed for the lifetime of the process — the Client exposes no runtime API to resize segments, change SSD paths, or toggle offloading. Any change to resource sizing requires restarting the Client. vLLM ranks never communicate with the Client directly — when a rank needs to store or load a block, it queries the Master for the block's location, and the Transfer Engine then moves the bytes peer-to-peer over RDMA between the rank and the Client's registered memory.

The two-tier storage within the Client works as follows:

- **Writes** land in CPU DRAM synchronously and are asynchronously persisted to SSD.
- **Reads** check DRAM first and fall through to SSD on a DRAM miss.
- **Eviction** is coordinated by the Master — when the DRAM pool hits the high watermark, the Master instructs the Client to spill blocks to SSD.

Decoupling storage from compute means the pool survives vLLM pod restarts, storage can be placed on nodes without GPUs (e.g., CPU-only nodes with large DRAM and NVMe), and the SSD tier is managed in one place per node rather than per GPU. See [`helpers/mooncake-client/`](https://github.com/llm-d/llm-d/tree/main/helpers/mooncake-client) for deployment manifests.

#### Content-Addressable Storage and PYTHONHASHSEED

KV cache blocks are stored with content-addressable keys derived from vLLM's block hash mechanism. Python randomizes its `hash()` seed per process by default — if two vLLM instances compute different hashes for the same input tokens, they can never share cached blocks.

All vLLM instances sharing a Mooncake Store **must** set `PYTHONHASHSEED` to the same fixed value:

```bash
PYTHONHASHSEED=0 vllm serve ...
```

#### mooncake_config.json Reference

Each vLLM instance requires a Mooncake configuration file, pointed to by the `MOONCAKE_CONFIG_PATH` environment variable.

**Embedded mode:**

```json
{
  "mode": "embedded",
  "metadata_server": "P2PHANDSHAKE",
  "master_server_address": "mooncake-master-store.mooncake.svc.cluster.local:50051",
  "global_segment_size": "80GB",
  "local_buffer_size": "4GB",
  "protocol": "rdma",
  "device_name": "",
  "enable_offload": false
}
```

**Standalone-store mode:**

```json
{
  "mode": "standalone-store",
  "metadata_server": "P2PHANDSHAKE",
  "master_server_address": "mooncake-master-store.mooncake.svc.cluster.local:50051",
  "global_segment_size": 0,
  "local_buffer_size": "4GB",
  "protocol": "rdma",
  "device_name": "",
  "enable_offload": true
}
```

| Field | Description |
| :--- | :--- |
| `mode` | `"embedded"` (in-process DRAM pool) or `"standalone-store"` (external client) |
| `metadata_server` | `"P2PHANDSHAKE"` for direct peer-to-peer metadata exchange |
| `master_server_address` | Mooncake Master gRPC endpoint (host:port) |
| `global_segment_size` | CPU memory per GPU contributed to pool. Must be > 0 in embedded, 0 in standalone-store |
| `local_buffer_size` | Private buffer per GPU for its own operations |
| `protocol` | `"rdma"` (production) or `"tcp"` (fallback) |
| `device_name` | RDMA device name (e.g., `"mlx5_0"`). Empty string for auto-discovery |
| `enable_offload` | Enable SSD/NVMe staging. Must match `mooncake_master` and `mooncake_client` flags |

#### Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MOONCAKE_CONFIG_PATH` | (required) | Path to `mooncake_config.json` |
| `PYTHONHASHSEED` | (random) | Must be set to same fixed value across all instances sharing the store |

For deployment recipes, see the [Tiered Prefix Cache Guide — Mooncake Store](../../../../guides/tiered-prefix-cache/modelserver/gpu/vllm/mooncake-store).

### Other Connectors

Out-of-tree engines coexist with the native path through a common integration contract on the llm-d side:

- **Serving-stack side** — each engine is already connector-compatible with one or more of vLLM, SGLang (via HiCache), and TensorRT-LLM, so the model server drives lookups, stores, and loads through its standard KV-cache connector API.
- **Scheduling side** — connectors integrate with llm-d through **KV-Events**: cache mutation notifications that the [KV-Cache Indexer](./kv-indexer.md) consumes to maintain a global view of cache distribution, enabling prefix-aware routing regardless of which backend is in use.

> [!NOTE]
> llm-d's deployment guides cover LMCache and Mooncake Store today. The integration pattern is the same for KVBM and other connector-compatible engines — they work out-of-the-box on the serving-stack side.

For deployment recipes, see the [Tiered Prefix Cache Guide](../../../../guides/tiered-prefix-cache).

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

### Distributed Offloading with MooncakeStoreConnector

At the vLLM server layer you might serve with the following configurations:

```yaml
args:
  - "--model=Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--kv-transfer-config"
  - '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_both"}'
env:
  - name: MOONCAKE_CONFIG_PATH
    value: /etc/mooncake/mooncake_config.json
  - name: PYTHONHASHSEED
    value: "0"
```

For the full `mooncake_config.json` reference, see [MooncakeStoreConnector — mooncake_config.json Reference](#mooncake_configjson-reference) above.

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

**Distributed offloading (MooncakeStoreConnector):** Best when cross-instance cache sharing is critical and RDMA networking is available. Eliminates the shared filesystem dependency and provides built-in eviction via the Mooncake Master. Embedded mode is recommended for most deployments; standalone-store mode adds complexity but enables the SSD tier and decouples storage from compute. Requires RDMA for production performance — TCP fallback is available but significantly slower.

## Further Reading

- [Tiered Prefix Cache Guide](../../../../guides/tiered-prefix-cache) — Step-by-step deployment guides
- [llm-d KV-Disaggregation Roadmaps](https://github.com/llm-d/llm-d-kv-cache/issues?q=is%3Aissue%20state%3Aopen%20label%3Aroadmap) — Planned features and improvements across offloading and KV-cache management
- [llm-d FS Backend](https://github.com/llm-d/llm-d-kv-cache/tree/main/kv_connectors/llmd_fs_backend) — Implementation details, configuration, and metrics
- [vLLM KV Offloading Connector](https://vllm.ai/blog/kv-offloading-connector) — Deep dive into vLLM's native offloading
- [Mooncake Store](https://github.com/kvcache-ai/Mooncake) — Upstream Mooncake project and documentation
- [vLLM MooncakeStoreConnector Usage Guide](https://docs.vllm.ai/en/v0.23.0/features/mooncake_store_connector_usage/) — vLLM-side configuration reference
