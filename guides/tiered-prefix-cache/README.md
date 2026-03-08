# Well-lit Path: Prefix Cache Offloading

## Overview

Efficient caching of prefix computation states to avoid recomputation is crucial for boosting Large Language Model (LLM) inference performance such as Time to First Token (TTFT) and overall throughput, as well as reducing the cost.
For the self-attention mechanism, the generation of the next token leverages the prefix Key & Value (KV) tensors.
For State Space Model (SSM) models such as mamba models, reusing cache of its SSM states of prefix locations also saves computation for the next token.
In this guide we use the term "prefix cache" to refer to the cache of computation states in the prefix tokens of a target token which includes the caching of prefix KV tensors and other forms of caches.
The prefix aware request scheduling optimizations in the [inference scheduling](../inference-scheduling/README.md) also applies here.

State of the art inference engines already implement native prefix cache reuse across requests in accelerator High-Bandwidth Memory (HBM), but in most serving environments HBM is already a constrained resource. To increase the amount of available memory beyond HBM requires more cache storage, driving the need for offloading prefix cache from HBM to more cost effective storage options such as CPU RAM.

This well-lit path offers multiple sub-guides per the cache storage type, either used standalone, or combined with other storage types in a tiered cache hierarchy. It also provides high level guidance on their suitability per workload, and makes recommendations about selecting and configuring a prefix cache offloading implementation.

## Storage Types

### CPU RAM

Enabling prefix cache offloading to CPU is recommended for the following reasons:

* Little operational overhead.
* There are usually more CPU RAM storage available than accelerator HBM on the host offering much larger cache capacity.
* CPU - accelerator transfer is faster than recomputation for most cases.
* (WIP) Prefix cache storage tier aware inference scheduling makes smart decisions based on cache tier (accelerator HBM vs. CPU RAM).

In low cache size scenario where HBM is primarily used, async CPU offloading should incur little overhead. In high cache size scenario loading cache from CPU RAM offers significantly higher cache hit and thus better performance than HBM only.

See the [CPU offloading guide](./cpu/README.md) to learn how to enable CPU RAM offloading with llm-d.

### Local Disk

Utilizing local disk storage can significantly increase the cache capacity. However disks are typically significantly slower than CPU RAM.

Consider this when:

* your workload can tolerate the latency overhead.
* the cache capacity of local disks is sufficient for your use case.

Otherwise we recommend a shared storage because it:

* offers cache sharing between instances,
* has more options to choose from to get a good tradeoff between cost and performance,
* offers significantly larger capacity.

To enable local disk offloading, refer to the [**Storage Offloading Guide**](./storage/README.md). The guide uses a generic storage connector that can connect to both local and remote/shared storage backends.

### Shared Storage

Offloading prefix cache to a shared (remote) storage tier provides several important benefits beyond local CPU or disk caching:

* **Extended cache capacity** - Offers massive storage capacity that is independent of the inference engine deployment size.
* **Shared KV-cache across nodes** - Multiple inference replicas can access and reuse the same prefix cache.
* **Fast scale-up** - New nodes can immediately reuse existing KV-cache data without warming the cache from scratch.
* **Persistence across restarts or failures** - KV-cache data survives pod restarts, rescheduling, and node failures.
* **Enterprise storage integration** - Can leverage mature enterprise storage systems (for example CephFS, GCP Lustre, IBM Storage Scale) with built-in durability, monitoring, and access control.

However, shared storage introduces additional operational and performance considerations. Latency and throughput depend on the characteristics of the underlying storage system, so careful evaluation is required to ensure that cache transfer overhead does not negatively impact inference performance.

Integration between the storage system and llm-d is achieved through vLLM connectors. The specific connector and data path depend on the storage system type and the underlying transport mechanism. 
For example, different implementations may use CPU staging buffers, GPU Direct Storage (GDS), or NIXL-based data movement.
Any storage connector that is compatible with vLLM can be used **transparently within the llm-d project**.

To enable shared storage offloading, refer to the [**Storage Offloading Guide**](./storage/README.md).

### P2P Cache Sharing

A P2P network can be formed between the inference engine instances to share caches in HBMs or CPU memory. It enables more cache sharing without needing additional storage resources. However this strategy adds operational overhead, and potential contention between model parallelism traffic such as tensor parallelism. We will add more recommendations in the following releases.

## Cache Tiering

Generally multiple cache tiers can be applied ordered by their cache read/write latencies, allowing frequently accessed caches to stay as close as possible to the accelerator, and large or less frequently accessed caches to be offloaded to slower tiers. We recommend always setting up HBM and CPU RAM tiers, and consider a third or fourth tier when your cache needs goes beyond HBM + CPU RAM.

## Cache Architecture

### Overview

When a request arrives, vLLM hashes the prompt prefix and checks each cache tier from fastest to slowest. On a hit, it loads the KV blocks and skips recomputation entirely. On a miss, it falls through to the next tier, and if nothing is found, runs a full prefill. After prefill, the computed KV blocks are written back to all configured tiers asynchronously — the GPU is never blocked waiting on I/O.

All of this is wired through vLLM's `--kv-transfer-config` flag. llm-d provides two connector implementations that plug into this interface: the **vLLM native OffloadingConnector** and the **LMCache connector**. Both are transparent to the inference scheduler and the rest of the llm-d stack.

### Cache Hierarchy

The cache is organized as an ordered set of tiers by latency and capacity. Hot blocks stay close to the GPU; cold or overflow blocks spill down to cheaper, larger storage:

```
Tier 1 — Accelerator HBM       (fastest, smallest)
Tier 2 — CPU RAM                (fast, larger)
Tier 3 — Local Disk / Shared Storage  (slowest, largest)
```

| Tier | Storage | Typical Capacity | Latency | Cross-node Sharing |
|------|---------|-----------------|---------|-------------------|
| HBM | GPU VRAM | 24–80 GB/GPU | ~µs | No |
| CPU RAM | Host memory | 200–500 GB/node | ~10µs | No |
| Local Disk | NVMe/SSD | 1–10 TB/node | ~100µs | No |
| Shared Storage | CephFS / Lustre / IBM Storage Scale | Petabyte-scale | ~1ms+ | Yes |

> **Recommendation**: Start with HBM + CPU RAM — it covers most workloads with minimal operational overhead. Add shared storage when your working set exceeds what fits across all replicas, or when you need cache to survive pod restarts and scale-up events.

### Request Flow

```
Incoming request
      │
      ▼
Hash prefix tokens
      │
      ▼
┌─────────────┐   HIT ──► load KV blocks from HBM ──► generate
│  Tier 1     │
│  HBM Cache  │
└──────┬──────┘
       │ MISS
       ▼
┌─────────────┐   HIT ──► async DMA: HBM ◄── CPU RAM ──► generate
│  Tier 2     │
│  CPU RAM    │
└──────┬──────┘
       │ MISS
       ▼
┌─────────────┐   HIT ──► async I/O: HBM ◄── storage ──► generate
│  Tier 3     │
│  Storage    │
└──────┬──────┘
       │ MISS
       ▼
  Full prefill
       │
       ▼
Write KV blocks back to all tiers (async, non-blocking)
```

### How the Connectors Interact

#### vLLM Native OffloadingConnector

The `OffloadingConnector` is built into vLLM upstream and requires no extra dependencies. It handles HBM ↔ CPU RAM transfers using GPU DMA, keeping interference with GPU compute minimal.

For a third storage tier, the **llm-d FS backend** extends it via the `SharedStorageOffloadingSpec` plugin. The plugin is installed as a Python wheel at pod startup and handles all POSIX I/O to the mounted storage path. It parallelizes reads and writes across 64 threads per GPU to maximize bandwidth. Eviction is not managed by the connector — that responsibility falls to the underlying storage system or an external [PVC Evictor](https://github.com/llm-d/llm-d-kv-cache).

```
vLLM
 └── OffloadingConnector
      ├── CPU RAM tier  (built-in, GPU DMA)
      └── llm-d FS backend (optional, POSIX I/O to PVC)
```

#### LMCache Connector

The `LMCacheConnectorV1` is a third-party connector from [lmcache.ai](https://lmcache.ai) that manages all three tiers in a single implementation. Tiers are configured via environment variables rather than vLLM args:

| Variable | Purpose |
|----------|---------|
| `LMCACHE_MAX_LOCAL_CPU_SIZE` | CPU RAM tier size in GB |
| `LMCACHE_LOCAL_DISK` | Storage tier mount path (`file:///mnt/...`) |
| `LMCACHE_MAX_LOCAL_DISK_SIZE` | Storage tier size in GB |
| `LMCACHE_EXTRA_CONFIG` | Extra options, e.g. `{"use_odirect":"True"}` |

The storage kustomization for LMCache builds directly on top of the CPU kustomization, forming a three-tier stack (HBM → CPU → storage) by composition. LMCache also exposes Prometheus metrics via `PROMETHEUS_MULTIPROC_DIR`.

```
vLLM
 └── LMCacheConnectorV1
      ├── CPU RAM tier  (LMCACHE_MAX_LOCAL_CPU_SIZE)
      └── Disk/storage tier  (LMCACHE_LOCAL_DISK)
```

### Data Flow Diagram

```
                        ┌─────────────────────────────────────────┐
                        │            Kubernetes Node               │
                        │                                          │
  Inference             │  ┌──────────────────────────────────┐   │
  Gateway  ────request──┼─►│           vLLM Process            │   │
                        │  │                                  │   │
                        │  │  ┌──────────────────────────┐   │   │
                        │  │  │   Tier 1: HBM KV Cache   │   │   │
                        │  │  │   (GPU VRAM, ~µs)        │   │   │
                        │  │  └────────────┬─────────────┘   │   │
                        │  │         miss  │  GPU DMA         │   │
                        │  │               ▼                  │   │
                        │  │  ┌──────────────────────────┐   │   │
                        │  │  │  Tier 2: CPU RAM Cache   │   │   │
                        │  │  │  OffloadingConnector or  │   │   │
                        │  │  │  LMCacheConnectorV1      │   │   │
                        │  │  │  (host memory, ~10µs)    │   │   │
                        │  │  └────────────┬─────────────┘   │   │
                        │  │         miss  │  async POSIX I/O │   │
                        │  │               ▼                  │   │
                        │  │  ┌──────────────────────────┐   │   │
                        │  │  │  Tier 3: Storage Cache   │   │   │
                        │  │  │  llm-d FS backend or     │   │   │
                        │  │  │  LMCache disk connector  │   │   │
                        │  │  │  (PVC mount, ~1ms+)      │   │   │
                        │  │  └────────────┬─────────────┘   │   │
                        │  └───────────────┼──────────────────┘   │
                        └─────────────────┼───────────────────────┘
                                          │ PVC (RWX)
                              ┌───────────▼───────────┐
                              │    Shared Storage      │
                              │  CephFS / Lustre /     │
                              │  IBM Storage Scale     │
                              └───────────┬───────────┘
                                          │
                              ┌───────────▼───────────┐
                              │   Other vLLM Replicas  │
                              │   (same PVC mount)     │
                              └───────────────────────┘
```

Cross-node KV cache sharing only happens at Tier 3 via a RWX PVC. CPU RAM and local disk are always node-local. This makes shared storage especially useful when scaling out — new pods can immediately reuse cached prefixes without any warmup.

### Connector Selection Guide

| Scenario | Recommended Connector |
|----------|----------------------|
| CPU offload only, minimal dependencies | `OffloadingConnector` (vLLM native) |
| CPU + shared storage offload | `OffloadingConnector` + llm-d FS backend |
| CPU + local disk offload | `LMCacheConnectorV1` |
| CPU + shared storage + Prometheus metrics | `LMCacheConnectorV1` |
| Cross-node KV cache sharing | Either connector with a RWX PVC |
