# Multimodal Serving

Serving multimodal (text + image / video) requests on llm-d over the same routing fabric as text.

## Overview

A multimodal prompt carries large encoded media segments (an image is ~1500 vision tokens for a typical image at the default model's tiling; video is larger) that are costly to render and frequently repeated. Routing a repeat request to a server that already holds the encoded prefix avoids re-encoding it:

- **TTFT**: repeat content skips the re-render on the request hot path.
- **GPU utilization**: reuse frees encoder and prefill cycles for new work.

The routing itself is modality-agnostic: it scores reuse over whatever multimodal items a request carries (image, video, audio). The modalities you can actually serve are set by the model; the guides below use a vision model (image + video); audio requires an omni model.

## Aggregated vs Disaggregated Multimodal Serving

These guides cover the **aggregated** path: encode, prefill, and decode are co-located on one model server, and multimodal affinity is EPP scoring over a single decode pool, the starting point for most deployments.

For **encode disaggregation** (a separate encoder pool feeding prefill/decode, for large media fan-out), see the [encode-disaggregation guide](https://github.com/llm-d/llm-d/pull/1614) (proposed, not yet merged).

## Shared Assumptions

- Gateway API Inference Extension (GAIE) CRDs installed. See each guide's Prerequisites for the install command and the `GAIE_VERSION` pin (currently `v1.5.0`).
- A multimodal-capable model server (vLLM with vision support).
- Client tooling per [helpers/client-setup](../../helpers/client-setup/README.md).
- An `llm-d-hf-token` secret for gated models.

## Guides

| Guide | Routing approach |
| ----- | ---------------- |
| [Optimized Baseline](./optimized-baseline/) | Load-aware plus approximate prefix-cache scoring estimated from request traffic. No render sidecar; start here. |
| [Precise Affinity](./precise-affinity/) | Exact per-pod multimodal cache-affinity scoring, via a render sidecar. For high content-reuse workloads. |

## Further Reading

- [llm-d-router architecture](https://github.com/llm-d/llm-d-router/blob/main/docs/architecture.md)
- [Precise Prefix Cache Routing](../precise-prefix-cache-routing/README.md): the text-only analog of precise affinity.
