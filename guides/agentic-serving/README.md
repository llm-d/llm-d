# Agentic Serving

The **agentic-serving** guide is a horizontal, workload-centric umbrella that serves
agentic *programs* on llm-d. It provides the recommended, cohesive deployment for the workload —
composing llm-d's well-lit paths into one stack rather than enabling a single feature.
For the workload model, canonical shapes, and the direction this guide is driving toward, see the
[Agentic Serving workload page](../../docs/well-lit-paths/workloads/agentic-serving.md); this guide is the
operational counterpart, and the canonical guide of the llm-d Agentic Inference SIG.

The reference workload this guide optimizes for is **long-horizon loops** (agentic code generation): deep multi-turn
sessions over large, repository-scale contexts with tool-call pauses between turns. Three
behaviors drive every choice below — prefill-heavy/decode-light (a 160K-token context dominates
TTFT), high reusable locality (cache hit rate, not FLOPs, sets throughput), and bursty/stateful
arrivals (tool pauses leave sessions idle, then resume in bursts).

## The Optimization Stack

This guide's deployment composes llm-d's capability paths into one stack, each layer relieving a
specific pressure of the agentic workload:

| Layer | What it does for the workload |
| :--- | :--- |
| **[Optimized baseline](../optimized-baseline/README.md)** — routing foundation | Prefix-cache scorer routes a turn to the replica already holding its prefix; load-aware scorers keep bursts off hot replicas. The foundation every deployment builds on. |
| **[Tiered KV offloading](../tiered-prefix-cache/README.md)** | Offload KV cache beyond accelerator memory across tiers, so idle sessions restore on resume instead of recomputing prefill. |
| **[Precise prefix-cache routing](../precise-prefix-cache-routing/README.md)** — advanced | An exact, global view of cache state, enabling session-centric orchestration and non-naive (beyond-LRU) KV-cache offloading & retention. |
| **[P/D disaggregation](../pd-disaggregation/README.md)** — large models / interactivity | Separate prefill and decode pools so heavy prefill never stalls token generation, stabilizing ITL. |

The [Agentic Inference SIG northstar](https://docs.google.com/document/d/1DCUVHp9Z8CZUnKiP04nnD_31M3gRishW-cWZ657Cn5U)
sets the broader direction: **session-graph orchestration**, **program-aware scheduling**,
**zero-recompute state reuse** with typed retention, and **proactive state placement** ahead of
fan-out. See the [workload page](../../docs/well-lit-paths/workloads/agentic-serving.md#direction) for the full
direction and further reading.

## Deployments

The layers above compose into deployments spanning a range of capabilities and operational
costs - from a routing-and-offloading baseline up to disaggregated serving, added incrementally
as a workload's scale and latency targets grow.

The reference workload is the same across deployments — agentic code generation (see above) — so
the sub-guides below differ along one axis: the **accelerator** they target (and the model and
serving topology that fit it). Each is listed as *model on accelerator*. Pick the one matching
your hardware:

- [NVIDIA-Nemotron-3-Ultra-550B on H200](nemotron-3-ultra-550b-h200.md) — P/D-disaggregated serving on 8× H200, with CPU KV-offloading and ready-to-use coding-agent client configs.
- [Qwen3-Coder-480B on TPU v7](qwen3-coder-480b-tpu.md) — routing + CPU KV-offloading on 8× TPU v7x (2x2x1).

## Benchmarking

Each deployment is benchmarked against a realistic agentic workload — large reused contexts
and bursty, locality-heavy traffic — replayed with
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) via the
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) harness, so cross-request and
cross-turn prefix reuse is actually exercised rather than assumed. The exact preset is tuned per
deployment (model, accelerator, and serving topology) rather than forced to be identical — see
each sub-guide for its workload. Deployments are compared on program-level metrics — whole-session
completion time and task throughput alongside TTFT and ITL. Replaying real agentic traces (program
structure and tool-call timing from OpenTelemetry) is the direction for program-level evaluation.
