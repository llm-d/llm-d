# Enable P2P Reuse

Prefix caches are per-pod, but the content they hold is often fleet-wide:
shared system prompts, common documents, session histories. Prefix-aware
routing sends each request to the pod that caches its prefix - yet routing
cannot always follow the cache: a hot prefix's owner saturates, a working
set outgrows any single pod, a session is rebalanced. Every one of those
requests recomputes KV tensors that already exist on a peer.

P2P reuse closes that gap: any model server pulls cached prefix KV blocks
directly from a peer's CPU offload tier instead of recomputing them. The
transfer is CPU-to-CPU over NIXL - the source pod's GPU is never touched,
so serving a pull costs the source no prefill capacity.

- Without P2P reuse, a request placed off the caching pod recomputes:

```
   ┌───────┐            ┌─────────┐            ┌───────────┐
   │user A │            │ pod 1   │            │  user A   │
   │  req  │            │ caches  │            │ follow-on │
   │ pod 1 │            │ prefix  │            │ req→pod 2 │
   └───┬───┘            └────┬────┘            └─────┬─────┘
       │                     │                       │
───────●─────────────────────●───────────────────────●───────▶ time
       │                     │                       │
       t                    t+a                     t+b
       │                     │                       │
       │◄─ KV live on pod 1 ─┼──────────────────────►│ ✗ (wrong pod)
                                                     ▼
                                               ┌────────────┐
                                               │ RECOMPUTE  │
                                               │  PREFILL   │
                                               └────────────┘
```

- With P2P reuse, the request pulls the prefix from its peer:

```
   ┌───────┐            ┌─────────┐            ┌───────────┐
   │user A │            │ pod 1   │            │  user A   │
   │  req  │            │ caches  │            │ follow-on │
   │ pod 1 │            │ prefix  │            │ req→pod 2 │
   └───┬───┘            └────┬────┘            └─────┬─────┘
       │                     │                       │
───────●─────────────────────●───────────────────────●───────▶ time
       │                     │                       │
       t                    t+a                     t+b
       │                     │                       │
       │◄─ KV live on pod 1 ─┼──────────────────────►│ ✓
                                                     ▼
                                               ┌────────────┐
                                               │ PULL FROM  │
                                               │ POD 1 CPU  │
                                               └────────────┘
```

> [!IMPORTANT]
> P2P reuse builds on the [Tiered Prefix Cache](tiered-prefix-cache.md)
> path: peers serve pulls from their CPU offload tier, so the tier must be
> enabled and sized at least as large as the per-pod GPU KV cache. Block
> hashes must agree across pods (identical `--block-size` and
> `PYTHONHASHSEED` fleet-wide) or every lookup silently misses.

## When It Pays

Pulling and recomputing are alternatives with a measurable crossover:
recompute cost grows with prefix length while the pull is a near-flat
CPU-to-CPU copy. On `openai/gpt-oss-120b` (H200) the pull wins at every
length from 2K tokens (-31%) to 48K (-68%); on Llama-3.1-8B the lines
cross near 2K tokens. The router only requests a pull when a peer holds at
least `minCachedTokenDelta` more prefix tokens than the scheduled pod - set
it from the measured crossover.

The regime rule from the benchmarks:

- **Working set fits the fleet's GPU caches** - prefix-aware routing alone
  is optimal; a local hit is free. The P2P producer stays quiet and costs
  nothing.
- **Long prefixes oversubscribe the caches, or a hot prefix saturates its
  owner** - placement by cache location pays in queues and recomputes;
  load-aware placement plus the pull serves the same content from the whole
  fleet.

## Deploy

See the [P2P KV Cache Sharing guide](../../../guides/p2p-kv-cache-sharing)
for manifests, verification gates, and step-by-step deployment.

## Architecture

1. **Model server pods publish KV-cache events** and run vLLM's
   `OffloadingConnector` with a CPU tier plus a P2P secondary tier: every
   pod both offloads computed KV to CPU and serves it to peers on the P2P
   port.
2. **The router builds the precise prefix index** from the KV events
   (the [Precise Prefix Cache Routing](precise-prefix-cache-routing.md)
   mechanism), so it knows per request which pods hold which prefix blocks.
3. **The `p2p-source-producer` compares** the best-cached peer against the
   pod scheduling picked; when the peer leads by at least
   `minCachedTokenDelta` tokens it sets the KV cache source header.
4. **The routing sidecar injects `kv_transfer_params.p2p`** from the
   header, and the engine pulls the prefix blocks from the peer's CPU tier
   over NIXL. Hits load as normal cache hits; misses recompute, so a
   partial or failed transfer degrades to baseline behavior instead of
   failing the request.

Under P/D disaggregation the same mechanism applies to the prefill leg:
the sidecar runs with `--enable-p2p-pull` and prefill workers pull cached
prefixes from peers before computing the remainder.

## Further Reading

- [P2P KV Cache Sharing guide](../../../guides/p2p-kv-cache-sharing) - manifests, verification gates, benchmarking.
- [Benchmark report: gpt-oss-120b on H200](../../../guides/p2p-kv-cache-sharing/benchmark-results/gpt-oss-120b-h200.md) - crossover, shared-prefix pools, and the document Q&A comparison against precise prefix routing.
- [Tiered Prefix Cache](tiered-prefix-cache.md) - the offload tiers P2P serves from.
- [Precise Prefix Cache Routing](precise-prefix-cache-routing.md) - the index that selects the pull source.
