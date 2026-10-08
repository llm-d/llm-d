# Agentic Serving

Agents are becoming the dominant shape of production LLM traffic. A single user goal expands into
a long *program* of model calls interleaved with tool execution — coding agents, deep-research
loops, tool-using copilots, multi-agent pipelines. But inference stacks are still tuned for the
isolated request: each call is served as if it is standalone, so the stack recomputes context it has
already seen, evicts long-lived sessions under memory pressure, and hot-spots whichever replica
holds a popular prefix.

Three properties of agentic workloads break those request-centric assumptions:

- **Massive context reuse** — branches of a reasoning tree, turns of a tool loop, and children of
  a swarm share most of their context (system prompts, tool definitions, conversation so far).
- **Program-level objectives** — users care about whole-program completion time, not the latency
  of any single call; one stalled branch can hold up the entire program.
- **Typed, predictable lifecycles** — durable system prompts and tools versus ephemeral
  scratchpad thoughts, with predictable flow the stack could anticipate but instead reacts to.

The next large efficiency gain for these workloads does not come from micro-optimizing isolated
requests — it comes from making the stack understand the structure of the agentic program, with
**model state — today primarily the KV-cache — as the medium that coordinates it.**

```text
Request-centric (today's default)        Program-aware (the direction)
─────────────────────────────────        ─────────────────────────────
 turn 1 ─▶ prefill 160K  ─▶ decode         turn 1 ─▶ prefill 160K ─▶ decode
 turn 2 ─▶ RE-prefill    ─▶ decode         turn 2 ─▶ reuse cache  ─▶ decode
 turn 3 ─▶ RE-prefill    ─▶ decode         turn 3 ─▶ reuse cache  ─▶ decode
   (KV evicted between turns)                (KV retained / offloaded, restored on resume)
```

A single engine already reuses cache across turns when a session happens to land on the same
replica with free memory; the failure is fleet-level — no cross-replica coordination, eviction
blind to which blocks a live session still needs, and no notion of the program behind the
requests.

## Canonical Workloads

The llm-d project anchors its work to concrete workload shapes, each stressing the
stack differently:

- **Long-horizon loops** (coding agents, computer use): one agent iterating reason → act →
  observe over many turns. Stresses cross-turn KV persistence, typed retention, and context
  growth.
- **Parallel fan-out** (best-of-N, tree search, RL rollouts, subagents): one shared context
  spawns many concurrent children. Stresses prefix reuse, proactive replication, and join
  (slowest-child) latency.
- **Multi-agent pipelines** (programs spanning distinct agents across pods, sequential or
  fork/join). Stresses reuse and program identity spanning pools, and program-level accounting.
- **Reasoning-heavy generation** (long internal reasoning before answering): shifts work to
  decode and grows per-request KV footprint.

## The Optimization Stack

The reference workload llm-d optimizes for today is **long-horizon loops** (agentic code
generation): deep multi-turn sessions over large, repository-scale contexts with tool-call pauses
between turns. Three behaviors drive every choice — prefill-heavy/decode-light (a 160K-token
context dominates TTFT), high reusable locality (cache hit rate, not FLOPs, sets throughput), and
bursty/stateful arrivals (tool pauses leave sessions idle, then resume in bursts).

The recommended deployments compose llm-d's foundations into one stack, each layer relieving a
specific pressure of the agentic workload:

| Layer | What it does for the workload |
| :--- | :--- |
| **[Optimized baseline](../../../guides/optimized-baseline/README.md)** — routing foundation | Prefix-cache scorer routes a turn to the replica already holding its prefix; load-aware scorers keep bursts off hot replicas. The foundation every deployment builds on. |
| **[Tiered KV offloading](../../../guides/tiered-prefix-cache/README.md)** | Offload KV cache beyond accelerator memory across tiers, so idle sessions restore on resume instead of recomputing prefill. |
| **[Precise prefix-cache routing](../../../guides/precise-prefix-cache-routing/README.md)** — advanced | An exact, global view of cache state, enabling session-centric orchestration and non-naive (beyond-LRU) KV-cache offloading & retention. |
| **[P/D disaggregation](../foundations/pd-disaggregation.md)** — large models / interactivity | Separate prefill and decode pools so heavy prefill never stalls token generation, stabilizing ITL. |

## Deploy

The stack is realized per model and accelerator as [Models](../models/README.md) guides,
each benchmarked against an agentic code-generation workload with large reused contexts and
bursty, locality-heavy traffic. Pick by hardware, then by topology:

- [GLM-5.2-FP8 on H200](../../../guides/glm-5-2/README.md) — wide expert-parallel P/D-disaggregated serving with MTP and tiered KV-offloading; the default uses 8 H200 nodes, with alternatives from 2 to 10 nodes. See the [GLM-5.2 blog post](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d) for the benchmark analysis.
- [NVIDIA-Nemotron-3-Ultra-550B on H200](../../../guides/nemotron-3-ultra/README.md) — P/D-disaggregated serving (TP=8) on 8× H200, with CPU KV-offloading and ready-to-use coding-agent client configs.
- [Qwen3-Coder-480B on TPU 7x](../../../guides/qwen3-coder-480b/README.md) — routing + CPU KV-offloading on 8× TPU 7x (2x2x1).

The Nemotron and Qwen deployments are benchmarked with
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) through
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark); GLM-5.2 uses
[`aiperf`](https://github.com/ai-dynamo/aiperf) on production agentic traces. Workload details
and metrics differ by model, accelerator, and topology, so compare results within each guide.
The guides report request-level throughput, TTFT, and ITL, plus session or task metrics where
the benchmark provides them; replaying complete agent dependency graphs with tool timing and
sub-agent fan-out remains a separate evaluation mode.

## Direction

The [llm-d project agentic northstar][northstar] sets the direction toward serving that is
*program-aware* rather than request-aware, treating the **session as a graph of typed state
blocks** the control plane plans over:

- **Session-graph orchestration** — model a session as a graph of typed blocks (system prompt,
  tool catalog, conversation turn, reasoning branch), built from external hints (e.g. Anthropic
  `cache_control`, OpenAI `prompt_cache_key`) where available and inferred otherwise. Precise
  KV-state indexing is the substrate this rides on.
- **Program-aware scheduling** — schedule based on program-level metrics and the critical path that
  stalls the whole loop, not per-request fairness. Extends the llm-d Router.
- **State reuse and lifecycle ("zero-recompute")** — if a reusable context exists anywhere (HBM,
  host memory, storage), no node recomputes it whenever reuse is cheaper than regeneration;
  durable context is pinned and dead branches dropped via typed retention. Builds on
  KV-disaggregation, tiered offloading, and KV-centric stores such as Mooncake.
- **Proactive state** — when an agent fans out, context is pre-positioned where the new compute
  will land rather than pulled on demand, with placement co-planned with scheduling from the
  session graph and declared fan-out.

The contract is two-layer: applications express intent through the caching and metadata APIs
they already use, and llm-d does the cross-session, cluster-wide work underneath — better hints
sharpen the orchestration, but the stack functions on inferred structure without them. Agent
control flow and cognitive decisions stay in the logic layer (agent frameworks, MCP and Skills
servers); llm-d provides the hooks to serve them efficiently.

The guiding boundary: **the engine is the mechanism, llm-d is the meaning** — the semantic layer
lives one level above the engine, so the work spans vLLM, SGLang, and beyond.

[northstar]: https://docs.google.com/document/d/1DCUVHp9Z8CZUnKiP04nnD_31M3gRishW-cWZ657Cn5U
