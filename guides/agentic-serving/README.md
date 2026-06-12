# Agentic Serving

The **agentic-inference well-lit path** is a horizontal, workload-centric umbrella that serves
agentic *programs* on llm-d by composing the capability paths into a stack and exposing a ladder
of deployment options that trade complexity for capability. For the workload model, canonical
shapes, and the direction this path is driving toward, see the
[Agentic Inference well-lit path](../../docs/well-lit-paths/agentic-inference.md); this guide is
the operational counterpart, and the canonical guide of the llm-d Agentic Inference SIG.

The reference workload this guide optimizes for is **long-horizon loops** (agentic code generation): deep multi-turn
sessions over large, repository-scale contexts with tool-call pauses between turns. Three
behaviors drive every choice below — prefill-heavy/decode-light (a 160K-token context dominates
TTFT), high reusable locality (cache hit rate, not FLOPs, sets throughput), and bursty/stateful
arrivals (tool pauses leave sessions idle, then resume in bursts).

## The Optimization Stack

The guide composes llm-d's capability paths into layers that each relieve a specific pressure of
the agentic workload; the deployment options below compose them.

| Layer | What it does for the workload |
| :--- | :--- |
| **[Optimized baseline](../optimized-baseline/README.md)** — routing foundation | Prefix-cache scorer routes a turn to the replica already holding its prefix; load-aware scorers keep bursts off hot replicas. Every option starts here. |
| **[Tiered KV offloading](../tiered-prefix-cache/README.md)** | Offload KV cache beyond accelerator memory across tiers, so idle sessions restore on resume instead of recomputing prefill. |
| **[Precise prefix-cache routing](../precise-prefix-cache-routing/README.md)** — advanced | An exact, global view of cache state, enabling session-centric orchestration and non-naive (beyond-LRU) KV-cache offloading & retention. |
| **[P/D disaggregation](../pd-disaggregation/README.md)** — large models / interactivity | Separate prefill and decode pools so heavy prefill never stalls token generation, stabilizing ITL. |

These layers are the available subset of a larger direction. The
[Agentic Inference SIG northstar](https://docs.google.com/document/d/1DCUVHp9Z8CZUnKiP04nnD_31M3gRishW-cWZ657Cn5U)
drives toward *program-aware* serving — **session-graph orchestration**, **program-aware
scheduling**, **zero-recompute state reuse** with typed retention, and **proactive state
placement** ahead of fan-out; precise routing and tiered offloading are the first steps. See the
[well-lit path page](../../docs/well-lit-paths/agentic-inference.md#direction) for the full
direction and further reading.

## Deployment Options

The layers above compose into deployment options spanning a range of capability and operational
cost — from a routing-and-offloading baseline up to disaggregated serving — added incrementally
as a workload's scale and latency targets grow. Concrete, benchmarked options are landing in
this directory as sub-guides.

## Choosing an Option

> 🚧 Under construction — guidance on selecting and combining layers for a given workload and
> SLO target lands with the deployment options.

## Benchmarking

Deployment options are compared against the same realistic agentic workload — large reused
contexts, deep multi-turn sessions, and tool-call stalls — replayed with
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) via the
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) harness rather than a single-turn
shared-prefix stream, so cross-turn reuse, session persistence, and bursty resumption are
actually exercised. Options are compared on program-level metrics — whole-session completion
time and task throughput alongside TTFT and ITL. Replaying real agentic traces (program
structure and tool-call timing from OpenTelemetry) is the direction for program-level
evaluation.
