# Models

Model guides are fully tuned, benchmarked production recipes for serving a state-of-the-art
model on a specific accelerator. Where a [Foundation](../foundations/README.md) teaches a single
capability on a minimal footprint, a Model guide composes several foundations — wide expert
parallelism, P/D disaggregation, prefix-cache aware routing, KV-cache offloading — into one
self-contained deployment and backs it with benchmark results.

- **[DeepSeek-V4](../../../guides/deepseek-v4/README.md)**: `DeepSeek-V4-Pro` on GB200 NVL72 — wide expert-parallel P/D disaggregation over cross-node NVLink, with operating points from low latency to maximum throughput.
- **[GLM-5.2](../../../guides/glm-5-2/README.md)** *(optimized for agentic workloads)*: `GLM-5.2-FP8` on H200 — wide expert-parallel P/D disaggregation with MTP speculative decoding, dual-tier prefix-cache routing, and CPU+NVMe KV offloading; benchmarked on production agentic traces.
- **[NVIDIA Nemotron 3 Ultra](../../../guides/nemotron-3-ultra/README.md)** *(optimized for agentic workloads)*: `NVIDIA-Nemotron-3-Ultra-550B` on H200 — P/D disaggregation with disaggregation-aware prefix-cache routing and CPU KV offloading, plus ready-to-use coding-agent client configs.
- **[Qwen3-Coder-480B](../../../guides/qwen3-coder-480b/README.md)** *(optimized for agentic workloads)*: `Qwen3-Coder-480B-A35B-Instruct-FP8` on TPU 7x — prefix-aware routing and CPU KV offloading, with an experimental P/D-disaggregated configuration.

For how these recipes serve long, multi-turn agentic programs, see
[Agentic Serving](../workloads/agentic-serving.md).
