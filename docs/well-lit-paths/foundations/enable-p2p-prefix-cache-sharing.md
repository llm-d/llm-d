# [Experimental] Enable P2P Prefix Cache Sharing

Prefix caches are per-pod, but their content is often fleet-wide: shared
system prompts, common documents, session histories. Prefix-aware routing
sends each request to the pod that caches its prefix, but routing cannot
always follow the cache: a hot prefix's owner saturates, a working set
outgrows any single pod, a session is rebalanced. Those requests recompute
KV tensors that already exist on a peer.

P2P prefix cache sharing closes that gap: a model server pulls cached prefix KV blocks
from a peer's CPU offload tier instead of recomputing them. The transfer
is CPU-to-CPU over NIXL. The source pod's GPU is never touched, so serving
a pull costs the source no prefill capacity.

The pull fires when a request shares a prefix with an earlier one but is
scheduled to a different pod. Two requests share a prefix whenever they
begin with the same tokens: the next turn of a conversation, another
question against the same document, another session on a shared system
prompt. The first request's pod is the **KV cache source**: it computed
the prefix and holds a copy in its CPU tier. When the router schedules a
prefix-sharing request to a different pod, it names the source on the
request, and the scheduled pod - the **consumer** - pulls the prefix
instead of recomputing it:

```mermaid
sequenceDiagram
    participant R as llm-d router
    participant S as KV cache source pod<br/>(serves request 1, caches the prefix)
    participant C as consumer pod<br/>(serves request 2, prefix missing)
    R->>S: request 1
    Note over S: computes the prefix KV, caches it,<br/>offloads a copy to its CPU tier
    Note over R: request 2 arrives sharing request 1's prefix,<br/>but placement picks a different pod
    R->>C: request 2 + header naming the source pod
    alt without P2P prefix cache sharing
        Note over C: recomputes the full shared prefix
    else with P2P prefix cache sharing
        C->>S: request the prefix blocks
        S-->>C: prefix KV blocks, CPU tier to CPU tier over NIXL
        Note over C: computes only the remainder<br/>(request 2's unshared tokens)
    end
```

> [!IMPORTANT]
> P2P prefix cache sharing builds on the [Tiered Prefix Cache](tiered-prefix-cache.md)
> path: peers serve pulls from their CPU offload tier. The shipped
> configuration uses the same `--block-size`, `PYTHONHASHSEED`, and tensor
> parallel layout on every peer. Other layouts need a compatible peer
> session fingerprint; the guide documents the tensor-parallel and
> model-runner constraints. Size the CPU tier to retain useful blocks;
> making it larger than the per-pod GPU KV cache is the guide's recommended
> starting point, not a correctness requirement. The
> [guide's Best Practices](../../../guides/p2p-kv-cache-sharing/README.md#best-practices)
> covers each requirement, its sizing rule, and its failure mode.

## When It Pays

Recompute cost grows with prefix length. On the measured H200/RDMA setup,
the CPU-to-CPU pull also grows with prefix length, but much more slowly.
For `openai/gpt-oss-120b`, the pull was faster at every measured point from
2,048 to 49,152 tokens. The crossover is model-, hardware-, and
transport-specific. The router requests a pull only when the selected
source holds at least `minCachedTokenDelta` more prefix tokens than the
scheduled pod, so calibrate that value on the target deployment.

Whether the pull helps also depends on the placement policy in front of
it:

- **Ownership is stable and uncontended**: prefix-aware routing avoids the
  transfer. A local hit is cheaper than a pull, so the pull stays quiet.
- **A hot prefix saturates its owner, or the working set outgrows the
  caches**: load-aware placement plus the pull serves the same content
  from the whole fleet. On the warm 16-pod document Q&A benchmark this
  delivered 1.5x better p99 TTFT and 35% higher throughput than
  prefix-affinity routing.
- **GPU KV capacity itself is the bottleneck**: cache co-location can use
  capacity more efficiently. Concurrent same-prefix requests on one pod
  share one copy of the blocks; spreading pays a per-pod copy whether the
  prefix is pulled or recomputed.

The guide ships prefix affinity plus the pull as the general-purpose
default. Reach for load-aware placement plus the pull when many
concurrent sessions contend on their owner pods. Both regimes are
measured in the
[benchmark report](../../../guides/p2p-kv-cache-sharing/benchmark-results/gpt-oss-120b-h200.md).

## Deploy

See the [P2P KV Cache Sharing guide](../../../guides/p2p-kv-cache-sharing)
for manifests, verification gates, and step-by-step deployment.

## Architecture

1. **Model server pods publish KV-cache events** and run vLLM's
   `OffloadingConnector` with a CPU tier plus a P2P secondary tier: every
   pod both offloads computed KV to CPU and serves it to peers.
2. **The router builds the precise prefix index** from the KV events
   (the [Precise Prefix Cache Routing](precise-prefix-cache-routing.md)
   mechanism), so it knows which pods hold which prefix blocks.
3. **The `p2p-source-producer` selects a source** from the CPU-tier holders
   within one index block of the largest cached prefix, weighted to avoid
   concentrating pulls on a queued source. After scheduling, it sets the
   KV cache source header only when that source leads the computing pod by
   at least `minCachedTokenDelta` tokens.
4. **The routing sidecar injects `kv_transfer_params.remote_kv_source`**
   from the header, and the engine pulls the prefix blocks from the
   peer's CPU tier over NIXL. Hits load as normal cache hits; a failed
   lookup is reported as a miss and the scheduled pod computes the missing
   prefix locally.

Under P/D disaggregation, the prefill worker is the pull consumer because
it computes the prompt KV. A decode worker may be the source for generated
session history retained in its CPU tier. After prefill completes, the
normal NIXL P/D path transfers the request's KV to the selected decoder.

## Further Reading

- [P2P KV Cache Sharing guide](../../../guides/p2p-kv-cache-sharing) - manifests, verification gates, benchmarking.
- [Benchmark report: gpt-oss-120b on H200](../../../guides/p2p-kv-cache-sharing/benchmark-results/gpt-oss-120b-h200.md) - crossover, shared-prefix pools, document Q&A.
- [Tiered Prefix Cache](tiered-prefix-cache.md) - the offload tiers P2P serves from.
- [Precise Prefix Cache Routing](precise-prefix-cache-routing.md) - the index that selects the pull source.
