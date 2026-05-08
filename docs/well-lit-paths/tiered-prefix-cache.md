# Tiered Prefix Cache

Given the multi-turn nature of agentic workloads, prefix-cache re-use is a critical factor for high performance inference.

Model servers hold KV-caches in GPU RAM with an LRU eviction scheme. Once space runs out from other requests consuming the resources, the KV caches are evicted. Follow on requests then must recompute the prefill. However, rather than evicting KV caches from GPU memory, we can instead leverage other system resources such as CPU RAM, local NVMe drives, and network storage systems to hold the evicted KVs - pulling them back into GPU RAM on demand.

This increases the **KV-working set size**, growing the **receptive-field** (the amount of time KV caches are retained in the system).

- Without KV offloading:
```
   ┌───────┐           ┌─────────┐            ┌───────────┐
   │user A │           │ user A  │            │  user A   │
   │  req  │           │   KV    │            │ follow-on │
   │       │           │ evicted │            │    req    │
   └───┬───┘           └────┬────┘            └─────┬─────┘
       │                    │                       │
───────●────────────────────●───────────────────────●───────▶ time
       │                    │                       │
       t                   t+a                     t+b
       │                    │                       │
       │◄───── KV live ────►│ ✗                     │
                                                    ▼
                                              ┌────────────┐
                                              │ RECOMPUTE  │
                                              │  PREFILL   │
                                              └────────────┘
```

- With KV offloading:

```
   ┌───────┐           ┌─────────┐            ┌───────────┐
   │user A │           │ user A  │            │  user A   │
   │  req  │           │   KV    │            │ follow-on │
   │       │           │ offload │            │    req    │
   └───┬───┘           └────┬────┘            └─────┬─────┘
       │                    │                       │
───────●────────────────────●───────────────────────●───────▶ time
       │                    │                       │
       t                   t+a                     t+b
       │                    │                       │
       │◄─────────────── KV live ──────────────────►│ ✓
                                                    ▼
                                              ┌────────────┐
                                              │ PULL FROM  │
                                              │  CPU RAM   │
                                              └────────────┘
```


> [!IMPORTANT]
> CPU KV Cache offloading is low overhead and introduces ~no additional complexity. It can be enabled in almost all deployments. Storage offloading requires additional consideration.

## Deploy

See the [KV Cache Management guide](../../guides/tiered-prefix-cache) for manifests and step-by-step deployment.

## Architecture

llm-d leverages the following architectures for offloading.

### CPU KV Cache Offloading

vLLM pods are configured with `OffloadingConnector` and increased CPU memory requests (e.g., 400 GB). Evicted KV-cache blocks move to host CPU memory instead of being discarded, extending the effective cache size with negligible overhead. The EPP maintains a global index of which blocks exist on which pods and tiers, adding a second `prefix-cache-scorer` plugin for CPU-tier blocks.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../assets/cpu-offloading.svg" alt="CPU KV Cache Offloading">
  </picture>
</p>

### Storage KV Cache Offloading

vLLM pods mount a ReadWriteMany PVC backed by shared storage (Lustre, CephFS, or similar) at `/mnt/files-storage`. The `OffloadingConnector` is configured with a custom backend module (`llmd_fs_backend.spec`) that handles async I/O with GPU DMA transfers. This enables cross-pod cache sharing -- newly scaled pods can read existing cache immediately -- persistence across pod restarts, and capacity limited only by storage system size.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../assets/fs-offloading.svg" alt="Storage KV Cache Offloading">
  </picture>
</p>
