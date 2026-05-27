---
layout: blog
title: "Extend GPU Memory with CPU KV Cache Offloading in llm-d"
date: 2026-05-27
---

**Author:** Antonio Cardace

Large language model inference is increasingly limited by GPU high-bandwidth memory (HBM), not compute. Every active request stores Key and Value (KV) state attention from the prefill phase; as context lengths grow, batch sizes increase, and multi-turn agentic workloads reuse long prefixes, that cache pressure shows up as evictions, expensive recomputation, and queueing. llm-d's **CPU KV Cache Offloading** well-lit path addresses this by extending the effective KV cache into host CPU memory.

This post explains why CPU offloading matters, how llm-d integrates it end to end, and how to get started. For step-by-step manifests and verification, see the [CPU offloading guide](../../guides/tiered-prefix-cache/cpu/README.md). If your working set exceeds single-node CPU capacity or you need cross-replica cache sharing, see the separate [storage offloading blog](https://llm-d.ai/blog/native-kv-cache-offloading-to-any-file-system-with-llm-d) and [storage guide](../../guides/tiered-prefix-cache/storage/README.md).

## What problem does it solve?

Model servers keep prefix KV caches in GPU HBM and evict blocks under memory pressure using an LRU policy. When a follow-on request arrives after its prefix has been evicted, the server must recompute the entire prefill — wasting GPU cycles and increasing time to first token (TTFT), especially for:

- **Long-context workloads** where a single request's KV footprint exceeds available HBM
- **High-concurrency serving** where many concurrent requests compete for the same GPU memory
- **Multi-turn and agentic patterns** that resend growing conversation history or tool-call traces as shared prefixes

Cluster operators often have far more CPU DRAM available on each node than GPU HBM. A typical inference host might offer hundreds of gigabytes of host memory alongside tens of gigabytes per GPU. Without offloading, that CPU memory sits unused while the inference engine evicts useful cache entries.

CPU KV cache offloading moves evicted blocks from GPU to pinned host memory instead of discarding them. When a later request needs the same prefix, the blocks are loaded back asynchronously — typically faster than recomputing attention over the full context. This increases the **KV working set size** and widens the window during which prefix cache hits remain possible.

## How does it work?

### vLLM native CPU offloading

llm-d builds on vLLM's **`OffloadingConnector`**, which asynchronously transfers KV blocks between GPU HBM and CPU DRAM using hardware DMA. Transfers run in the background so they do not block the decode loop, and a contiguous KV layout (available in recent vLLM releases) improves transfer throughput by grouping all layers into single physical blocks.

The simplest vLLM configuration uses dedicated flags:

```bash
--kv-offloading-backend native --kv-offloading-size <size_in_GB>
```

Alternatively, pass the connector JSON directly:

```bash
--kv-transfer-config '{"kv_connector":"OffloadingConnector","kv_role":"kv_both","kv_connector_extra_config":{"cpu_bytes_to_use":214748364800}}'
```

The `cpu_bytes_to_use` field sets the CPU-side cache budget per vLLM instance. Size this based on your model, block size, and expected concurrent context — the [CPU offloading guide](../../guides/tiered-prefix-cache/cpu/README.md) includes a reference configuration for Qwen3-32B with 100 GB of CPU cache.

llm-d also supports **LMCache** and other connector-compatible engines for CPU offloading when you need an out-of-tree cache manager. See the [tiered prefix cache overview](../../guides/tiered-prefix-cache/README.md) for connector options.

## How do I use it?

### Prerequisites

You need a running llm-d deployment with the llm-d Router and vLLM model servers. If you are new to llm-d, start with the [getting started guide](../getting-started/quickstart.md) or the [optimized baseline guide](../../guides/optimized-baseline/README.md) for prefix-aware routing fundamentals.

### Enable CPU offloading

1. **Configure vLLM** with the `OffloadingConnector` and a CPU cache budget appropriate for your workload. Ensure the pod requests enough host memory to hold the configured offload size plus headroom for the runtime.

2. **Configure the llm-d Router** with tiered prefix-cache scoring (GPU and CPU prefix-cache scorers). Deploy using the Helm values and Kustomize overlays in the [CPU offloading guide](../../guides/tiered-prefix-cache/cpu/README.md).

3. **Verify** by sending completion or chat requests through the router and observing reduced TTFT on follow-on requests that share a prefix. Optional monitoring resources are documented in the guide.

### Example: model server flags

The llm-d CPU guide patches vLLM deployments with connector configuration similar to:

```yaml
args:
  - "Qwen/Qwen3-32B"
  - "--tensor-parallel-size=2"
  - "--kv-offloading-backend=native"
  - "--kv-offloading-size=100"
```

### When you should expect gains

Benchmarking in the llm-d CPU guide compares baseline vLLM against CPU offloading when the KV working set exceeds available HBM but fits within HBM plus CPU RAM. In that **high cache pressure** scenario on Qwen3-32B (4× NVIDIA H100, 100 GB CPU offload), enabling CPU offloading delivered approximately:

- **25% lower mean TTFT**
- **21% higher overall throughput**

When the working set already fits in HBM, results stay near baseline — confirming that CPU offloading is safe to enable proactively rather than only after you observe evictions.

For reproducible benchmark workflows, see the [benchmark helpers](../../helpers/benchmark.md) and the benchmarking section in the [CPU guide](../../guides/tiered-prefix-cache/cpu/README.md).

### What this well-lit path does not cover

CPU offloading extends per-node cache capacity. It does **not** share KV state across nodes or survive pod rescheduling on its own. For those requirements, add a [storage tier](../../guides/tiered-prefix-cache/storage/README.md) on top of CPU offloading. The llm-d project treats CPU and storage as complementary tiers in a prefix-cache hierarchy; CPU is the recommended starting point because of its simplicity.

## How do I help?

CPU KV cache offloading is an active well-lit path in llm-d. We welcome feedback from production deployments:

- Open issues or pull requests in [llm-d/llm-d](https://github.com/llm-d/llm-d)
- Join the discussion on [llm-d Slack](https://llm-d.slack.com)
- Attend [community calls](https://llm-d.ai/community) (Wednesdays, 12:30 PM ET)

For deeper architecture reference, see [KV-Cache Offloading](../architecture/advanced/kv-management/kv-offloader.md) and the [Tiered Prefix Cache well-lit path](../well-lit-paths/tiered-prefix-cache.md). For vLLM connector internals, see the [vLLM KV Offloading Connector blog](https://blog.vllm.ai/2026/01/08/kv-offloading-connector.html).
