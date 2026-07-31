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
the sub-guides below differ by the **accelerator** they target and the **serving topology** that
fits the model. Each is listed as *model on accelerator*; pick by hardware, then by topology:

- [NVIDIA-Nemotron-3-Ultra-550B on H200](nemotron-3-ultra-550b-h200.md) — P/D-disaggregated serving (TP=8) on 8× H200, with CPU KV-offloading and ready-to-use coding-agent client configs.
- [GLM-5.2-FP8 on H200](glm-5-2-h200.md) — wide expert-parallel P/D-disaggregated serving with MTP and tiered KV-offloading; the default uses 8 H200 nodes, with alternatives from 2 to 10 nodes. See the [GLM-5.2 blog post](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d) for the benchmark analysis.
- [Qwen3-Coder-480B on TPU v7](qwen3-coder-480b-tpu.md) — routing + CPU KV-offloading on 8× TPU v7x (2x2x1).

## Benchmarking

Each deployment is benchmarked against an agentic workload with large reused contexts and
bursty, locality-heavy traffic. The Nemotron and Qwen deployments use
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) through
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark); GLM-5.2 uses
[`aiperf`](https://github.com/ai-dynamo/aiperf). Workload details and metrics differ by model,
accelerator, and topology, so compare results within each sub-guide.

The guides report request-level throughput, TTFT, and ITL, plus session or task metrics where
the benchmark provides them. Replaying complete agent dependency graphs with tool timing and
sub-agent fan-out remains a separate evaluation mode.
