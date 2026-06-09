# Agentic Inference

The **agentic-inference well-lit path** is a horizontal, workload-centric umbrella that serves
agentic *programs* on llm-d by composing the capability paths into a stack and exposing a ladder
of deployment options that trade complexity for capability. For the workload model, canonical
shapes, and the direction this path is driving toward, see the
[Agentic Inference concept page](../../docs/well-lit-paths/agentic-inference.md); this guide is
the operational counterpart, and the canonical guide of the llm-d Agentic Inference SIG.

The reference workload here is **long-horizon loops** (agentic code generation): deep multi-turn
sessions over large, repository-scale contexts with tool-call pauses between turns. Three
behaviors drive every choice below — prefill-heavy/decode-light (a 160K-token context dominates
TTFT), high reusable locality (cache hit rate, not FLOPs, sets throughput), and bursty/stateful
arrivals (tool pauses leave sessions idle, then resume in bursts).

## The Optimization Stack

The guide composes llm-d's capability paths into layers that each relieve a specific pressure of
the agentic workload; the [deployment options](#deployment-options) below select among them.

| Layer | What it does for the workload |
| :--- | :--- |
| **[Optimized baseline](../optimized-baseline/README.md)** — routing foundation | Prefix-cache scorer routes a turn to the replica already holding its prefix; load-aware scorers (KV-utilization, queue) keep bursts off hot replicas. Every option starts here. |
| **[Tiered KV offloading](../tiered-prefix-cache/README.md)** — CPU → storage | Idle sessions spill from HBM to CPU RAM (low overhead) and to storage (RWX PVC; cross-pod sharing, persistence), so resumes restore instead of recomputing prefill. |
| **[Precise prefix-cache routing](../precise-prefix-cache-routing/README.md)** — advanced | Exact global KV-block index from KV-events, enabling session-centric orchestration and non-naive (beyond-LRU) retention rather than estimated reuse. |
| **[P/D disaggregation](../pd-disaggregation/README.md)** — large models / interactivity | Separate prefill and decode pools so heavy prefill never stalls token generation, stabilizing ITL. |
| **Tool-aware prefix caching** — cross-cutting | Prefix matching aware of chat/tool message structure, so tool-calling turns keep hitting cache. |

These layers are the available subset of a larger direction. The
[Agentic Inference SIG northstar](https://docs.google.com/document/d/1DCUVHp9Z8CZUnKiP04nnD_31M3gRishW-cWZ657Cn5U)
drives toward *program-aware* serving — **session-graph orchestration**, **program-aware
scheduling**, **zero-recompute state reuse** with typed retention, and **proactive state
placement** ahead of fan-out; precise routing and storage offloading are the first steps. See the
[concept page](../../docs/well-lit-paths/agentic-inference.md#direction) for the full direction
and further reading.

## Deployment Options

The options are reference configurations that select from the stack above, ordered by increasing
capability and operational cost. Both target the reference workload and are measured against the
same [benchmark](#benchmarking); pick the lowest rung that meets your SLOs, then layer in the
advanced optimizations as the workload grows.

| Option | Composes | Requirements & complexity | Best for |
| :--- | :--- | :--- | :--- |
| **[Routing + offload](./routing/README.md)** | Optimized-baseline routing + CPU-DRAM KV offload, single-host | Stock EPP scorers, single-host model servers. No disaggregation, no custom builds. **Lowest barrier.** | Prefix reuse and multi-turn memory headroom on a single accelerator pool. |
| **[Disaggregated + tool-aware](./disaggregated/README.md)** | + P/D disaggregation, NIXL KV transfer, tool-aware prefix caching | Separate prefill/decode pools, a KV-transfer fabric, and a custom router build for tool-aware caching. **Higher operational cost.** | Medium-large models, tight TTFT/ITL targets, heavy tool-calling. |
| **Advanced layers** *(compose onto either)* | + Precise prefix-cache routing, + storage KV offload | Precise: KV-event publishing/indexing. Storage: an RWX storage backend and I/O tuning. | Session-centric orchestration, beyond-LRU retention, and cross-replica reuse beyond host DRAM. |

### Routing + offload

The entry rung. Single-host replicas serve the full model under the optimized-baseline scorers
(prefix-cache weighted highest, alongside the queue and KV-utilization scorers), while a
KV-offload connector spills idle sessions from HBM to CPU DRAM. This captures the two biggest
wins — prefix reuse and a larger working set — with no disaggregation and no custom images.
See **[routing/](./routing/README.md)**.

### Disaggregated + tool-aware

The advanced rung, for when prefill interference or strict interactivity SLOs make a single pool
insufficient. Prefill and decode run as separate pools with KV transferred over NIXL, and the
router adds disaggregation-aware scheduling plus **tool-aware prefix caching** so tool-structured
turns still hit cache. Requires a KV-transfer fabric and a router build with tool-aware caching
enabled. See **[disaggregated/](./disaggregated/README.md)**.

### Advanced layers

Layer onto either option as the working set and the need for program-awareness grow. **Precise
prefix-cache routing** ([guide](../precise-prefix-cache-routing/README.md)) swaps approximate
prefix estimation for an exact global KV index, the basis for session-centric orchestration and
beyond-LRU retention. **Storage KV offloading** ([guide](../tiered-prefix-cache/README.md))
extends offload past host DRAM for working sets no single host can hold and for cross-replica
reuse. These map directly to the [northstar direction](../../docs/well-lit-paths/agentic-inference.md#direction).

## Benchmarking

Every option is evaluated against the same agentic workload so results are comparable across
rungs. The benchmark uses [`inference-perf`](https://github.com/llm-d/llm-d-benchmark) to replay
a realistic agentic workload — shared and dynamic system prompts, multi-turn sessions, and
tool-call stalls — rather than a single-turn shared-prefix stream. This exercises the exact
behaviors the stack is tuned for: cross-turn prefix reuse, session persistence under memory
pressure, and bursty resumption.

The workload template lives with the routing option at
[`routing/benchmark-templates/guide.yaml`](./routing/benchmark-templates/guide.yaml). Run it
against any deployed option by pointing `base_url`/`namespace` at that stack; see the
[benchmark helper](../../helpers/benchmark.md) for harness mechanics. Replay of real agentic
traces (program structure and tool-call timing from OpenTelemetry) is the direction for
program-level evaluation.

## Choosing an Option

Start with **routing + offload** if your model fits a single-host pool and the goal is to stop
re-prefilling shared context — the smallest change with the largest return for most agentic
deployments. Add **storage offload** once host DRAM can no longer hold the live session set, or
when newly scaled replicas should read existing cache immediately.

Move to **disaggregated + tool-aware** when prefill interference erodes inter-token latency, when
interactivity SLOs require isolating decode, or when tool-calling structure is splitting your
cacheable prefix.

Adopt **precise prefix-cache routing** when approximate scoring leaves reuse on the table and you
want the router to orchestrate placement and retention by session structure rather than recency.

## Prerequisites

Shared across all options (each sub-guide pins versions and option-specific variables):

- Have the [proper client tools installed](../../helpers/client-setup/README.md).
- Check out the repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

- A Kubernetes cluster with the accelerators your chosen option targets, and an
  [`llm-d-hf-token` secret](../../helpers/hf-token.md) in the target namespace.

Follow the install, verification, and cleanup steps in the option's own guide:
[routing/](./routing/README.md) · [disaggregated/](./disaggregated/README.md).

## Status

This umbrella is new and its rungs are landing incrementally. The routing and disaggregated
options are under active development ([#1727](https://github.com/llm-d/llm-d/pull/1727),
[#1740](https://github.com/llm-d/llm-d/pull/1740)); precise routing and storage offload exist as
capability paths and are being folded in as composable layers; the remaining canonical workload
shapes are on the roadmap ([#1558](https://github.com/llm-d/llm-d/issues/1558)). Default models,
hardware, and tuning in each option are starting points, not production guarantees — review and
adjust them for your environment.
