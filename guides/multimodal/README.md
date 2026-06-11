# Multimodal Guides

llm-d serves multimodal models (vision, audio, video) on the same routing fabric as text-only models, with multimodal-aware scoring layered on top of the prefix-cache and load-aware foundation.

Repeated multimodal content is a major reuse opportunity. A single 1080p image expands into roughly 1500 vision tokens before prefill; an audio clip of a few seconds expands into similar magnitudes. Routing a second request carrying the same image to the pod that already encoded it avoids redundant render and prefill, directly reducing time to first token (TTFT) and improving GPU utilization.

The guides below cover two flavors of multimodal-aware routing. Both are workload-first deployments; pick the one whose tradeoffs match your traffic.

## Choose a flavor

| Aspect | [Optimized Baseline](./optimized-baseline/README.md) | [Precise Affinity](./precise-affinity/README.md) |
|---|---|---|
| Token-producer mode | `estimate` (resolution-based heuristic) | `vllm.url` (real `/render`) |
| Cache scoring | `approx-prefix-cache-producer` + `prefix-cache-scorer` | `mm-embeddings-cache-producer` + `mm-embeddings-cache-scorer` |
| Routing signal | Approximate hashing of text+image content | Per-pod LRU tracking of actual multimodal item hashes |
| Render sidecar in EPP pod | Not required | Required |
| Best when | Mixed text+MM workloads where estimated routing is sufficient | High-cardinality MM reuse where deterministic per-content routing matters |
| Routing on identical content | Strong but probabilistic | Deterministic same-pod after the first request warms the cache |

If you are new to llm-d and serving a multimodal model for the first time, start with [Optimized Baseline](./optimized-baseline/README.md). It has no render-sidecar requirement and works well when content reuse is moderate.

If your workload has high content reuse (document processing pipelines, customer support flows referencing the same product photos, multi-turn vision conversations on the same image), [Precise Affinity](./precise-affinity/README.md) gives deterministic same-pod routing on identical content. It requires the EPP pod to include a vLLM render sidecar; in return the cache hit signal is exact rather than estimated.

For prefill/decode disaggregation and encode disaggregation on multimodal workloads, see the [Encode Disaggregation guide](https://github.com/llm-d/llm-d/pull/1614). Disaggregated configurations add separate pools for the encode and prefill stages and are out of scope for these aggregated guides.

## What both guides assume

- A Kubernetes cluster with GPU nodes capable of running the chosen multimodal model.
- Gateway API CRDs and the Gateway API Inference Extension (GIE) CRDs installed.
- A Gateway resource owning the listener for inference traffic. Any conformant Gateway implementation works; agentgateway is the reference.
- The llm-d Router (EPP) chart, version compatible with llm-d v0.8.0 or later.

## Further reading

- [llm-d-router architecture](https://llm-d.ai/docs/architecture) for EPP internals.
- [Optimized Baseline (text-only)](../optimized-baseline/README.md) for the foundation both multimodal guides build on.
- [Precise Prefix Cache Routing (text-only)](../precise-prefix-cache-routing/README.md) for the text-only sibling of the Precise Affinity flavor.
