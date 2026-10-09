# Models

Model guides are fully tuned, benchmarked production recipes for serving a state-of-the-art
model on a specific accelerator. Where a [Foundation](../foundations/README.md) teaches a single
capability on a minimal footprint, a Model guide composes several foundations — wide expert
parallelism, P/D disaggregation, prefix-cache aware routing, KV-cache offloading — into one
self-contained deployment and backs it with benchmark results.

- **[DeepSeek-V4](../../../guides/models/deepseek-v4/README.md)**: `DeepSeek-V4-Pro` on GB200 NVL72 — wide expert-parallel P/D disaggregation over cross-node NVLink, with operating points from low latency to maximum throughput.
- **[GLM-5.2](../../../guides/models/glm-5-2/README.md)** *(optimized for agentic workloads)*: `GLM-5.2-FP8` on H200 — wide expert-parallel P/D disaggregation with MTP speculative decoding, dual-tier prefix-cache routing, and CPU+NVMe KV offloading; benchmarked on production agentic traces.
- **[NVIDIA Nemotron 3 Ultra](../../../guides/models/nemotron-3-ultra/README.md)** *(optimized for agentic workloads)*: `NVIDIA-Nemotron-3-Ultra-550B` on H200 — P/D disaggregation with disaggregation-aware prefix-cache routing and CPU KV offloading, plus ready-to-use coding-agent client configs.
- **[Qwen3-Coder-480B](../../../guides/models/qwen3-coder-480b/README.md)** *(optimized for agentic workloads)*: `Qwen3-Coder-480B-A35B-Instruct-FP8` on TPU 7x — prefix-aware routing and CPU KV offloading, with an experimental P/D-disaggregated configuration.

## Agentic Workloads

Agents are becoming the dominant shape of production LLM traffic: one user goal expands into a
long *program* of model calls interleaved with tool execution (coding agents, deep-research
loops, multi-agent pipelines). Three properties break request-centric serving:

- **Massive context reuse** — turns of a tool loop and branches of a fan-out share most of their
  context (system prompts, tool definitions, the conversation so far).
- **Program-level objectives** — users care about whole-program completion time, not the latency
  of any single call.
- **Bursty, stateful arrivals** — tool pauses leave sessions idle, then resume them in bursts.

The guides marked *optimized for agentic workloads* above target the reference agentic workload,
long-horizon agentic code generation: deep multi-turn sessions over repository-scale contexts,
prefill-heavy and decode-light, where cache hit rate rather than FLOPs sets throughput. Each
composes the same stack, one layer per pressure:

| Layer | What it does for the workload |
| :--- | :--- |
| **[Optimized baseline](../../../guides/optimized-baseline/README.md)** | Prefix-cache scoring routes a turn to the replica already holding its prefix; load-aware scorers keep bursts off hot replicas. |
| **[Tiered KV offloading](../../../guides/tiered-prefix-cache/README.md)** | KV cache beyond accelerator memory, so idle sessions restore on resume instead of recomputing prefill. |
| **[Precise prefix-cache routing](../../../guides/precise-prefix-cache-routing/README.md)** | An exact, global view of cache state for session-centric routing and smarter KV retention. |
| **[P/D disaggregation](../../../guides/pd-disaggregation/README.md)** | Separate prefill and decode pools so heavy prefill never stalls token generation. |

Nemotron 3 Ultra and Qwen3-Coder-480B are benchmarked with
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) through
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark); GLM-5.2 replays production agentic
traces with [`aiperf`](https://github.com/ai-dynamo/aiperf). Workloads and metrics differ by
guide, so compare results within each guide.
