# Extending llm-d for Multimodal Inference at Scale

**Authors**: Luigi Mario Zuccarelli, Dean Kelly, Paul Power, Owen Corrigan, Sam Batschelet, Rishabh Saini

> **Status: Draft.** Target repositories: llm-d/router, llm-d/gateway, vllm-omni,
> llm-d, llm-d-incubation/coordinator. June 2026.

## Summary

Contribute production-validated multimodal serving capabilities to the llm-d open-source project, extending the platform from text-only LLM inference into a unified system that serves text, audio, image, and future modalities through a single Kubernetes-native stack.

The work spans three areas:

1. Prove that llm-d's core distributed techniques (disaggregated serving, prefix caching, intelligent routing, and variant autoscaling) generalize beyond text.
2. Deliver upstream "well-lit path" guides so any adopter can deploy multimodal workloads on llm-d.
3. Strengthen the platform's extensibility so new modalities can be added without architectural changes.

This RFC explicitly covers three model architecture categories supported by vLLM-omni:

- **Autoregressive omni models** (Qwen-Omni, GPT-4o)
  - Multi-LLM architectures (Thinker + Talker)
  - Disaggregation naturally maps onto pipeline stages.

- **Diffusion models** (Stable Diffusion, FLUX)
  - Iterative denoising pipelines.
  - Different execution model from autoregressive inference.

- **Specialized single-modality models**
  - Whisper (STT)
  - Bark / XTTS (TTS)
  - Traditional encoder-decoder or autoregressive architectures.

## Motivation

Multimodal inference is becoming a core requirement for modern AI applications, enabling a single serving platform to process text, images, audio, and future modalities.

While llm-d already provides well-lit paths for text-based LLM serving, support for multimodal workloads remains limited.

Examples include:

- Voice-enabled AI agents
- Multimodal RAG
- Creative applications
- Accessibility tools

Today these workloads require multiple independent infrastructure stacks, resulting in:

- duplicated components
- siloed operations
- inefficient GPU allocation

llm-d's vision explicitly includes support for emerging multimodal applications. This RFC extends the v0.8 multimodal foundations (MM-aware routing, EPD disaggregation, NIXL EC connector) into a production-ready upstream contribution.

### The Model Architecture Gap

Different model architectures require different serving strategies.

#### Omni Models

Examples:

- Qwen-Omni
- GPT-4o

Characteristics:

- Multiple internal LLMs
- Thinker + Talker pipeline
- Strong fit for prefill/decode disaggregation

#### Diffusion Models

Examples:

- Stable Diffusion
- FLUX

Characteristics:

- Iterative denoising
- Separate execution engine
- No KV cache
- Encode → Denoise → Decode pipeline

#### Specialized TTS

Examples:

- Bark
- XTTS
- F5-TTS

Characteristics:

- Decode-heavy workload
- Prefill/decode disaggregation offers limited benefit
- Queue-depth-aware routing is more valuable

#### Encoder-Decoder STT

Example:

- Whisper

Characteristics:

- Fixed encoder
- Autoregressive decoder
- Encoder is the dominant bottleneck

### Goals

- Validate llm-d's four core techniques across multimodal workloads.
- Deliver a `guides/multimodal-serving/` well-lit path.
- Extend Coordinator and Router/EPP with modality-aware routing.
- Orchestrate encode, prefill and decode as independent stages.
- Extend Variant Autoscaler across modalities.
- Upstream every change.
- Publish reproducible benchmarks.

### Non-Goals

- Standalone multimodal platform
- New Kubernetes CRDs
- Forking vLLM-omni
- Replacing Variant Autoscaler with HPA
- Video support
- Fine-tuning
- LoRA
- Client SDKs
- New model architectures

## Proposal

The work is organized into three parallel streams.

### Stream 1 – Multimodal Integration

- Deploy vLLM-omni inside InferencePool
- Audio routing
- Audio disaggregation via xPyD/NIXL
- Coordinator orchestration
- Custom routing hints

### Stream 2 – Platform Generalization

- Speech-to-text
- Image generation
- Variant autoscaling
- Production hardening
- Cross-modal prefix cache reuse

### Stream 3 – Upstream Contribution

- Router/EPP
- Coordinator
- Autoscaler
- Documentation
- Benchmarks

### Milestones

| Milestone | Deliverable |
|------------|-------------|
| MVP validation | Audio routing, stable mixed traffic |
| Disaggregation | xPyD via NIXL, P95 improvement |
| Production routing | Modality-aware EPP |
| Multi-modal autoscaling | STT + Image + Autoscaler |
| Well-lit path | Documentation merged upstream |

### User Stories

#### Inference Platform Team

> I want a single llm-d deployment that serves text, audio and image workloads from shared GPU pools.

#### Workload Author

> I want to call OpenAI-compatible audio and image endpoints through the existing llm-d gateway.

#### llm-d Contributor

> I want modality-aware routing implemented as EPP plugins rather than separate systems.

#### Omni Model Engineer

> I want Thinker and Talker automatically disaggregated.

#### Diffusion Engineer

> I want prompt encoding and denoising independently scheduled.

## Design Details

### High-Level Architecture

```
Client
    │
Coordinator
    │
Envoy Gateway
    │
Router / EPP (ext_proc)
    │
vLLM Workers
```

### Coordinator Boundary

The vLLM-omni engine orchestrates stages within a process.

The Coordinator owns:

- Inter-pod orchestration
- KV/EC cache transfer
- Conditional decode
- Pipeline routing

### Modality as a Workload Characteristic

Pods participate in the same InferencePool using labels such as:

```yaml
modality: omni
model-arch: omni-llm
```

The Router/EPP detects modality and selects the appropriate strategy.

### Architecture-Aware Disaggregation

#### Omni Models

- Thinker → Pod A
- Talker → Pod B
- KV transfer via NIXL

#### Diffusion Models

- Encode pod
- Denoise pod
- Latent transfer

#### TTS

Monolithic execution.

Optimizations:

- Queue depth
- GPU memory

#### STT

Prioritize encoder batching.

### Scoring Plugins

| Plugin | Primary Signal | Secondary Signals | Policy |
|---------|----------------|-------------------|--------|
| omni-llm | KV cache hit rate | Queue depth, GPU memory | xPyD |
| diffusion | Queue depth | GPU memory | Encode/Denoise |
| tts | Queue depth | GPU memory | Monolithic |
| stt | Queue depth | Encoder batching | Monolithic |
| text | KV cache hit rate | Queue depth | Existing |

### Cross-Modal Prefix Cache

Applicable only to omni models.

Example:

```
Text prompt
      │
Shared Thinker Encoder
      │
Reusable KV Cache
      ├── Text Output
      └── Audio Output
```

Not applicable to:

- Diffusion
- Single-modality models

### Variant Autoscaling

Architecture-specific metrics:

- Omni
  - P95 latency
  - Cache pressure

- Diffusion
  - Denoising queue depth

- Specialized
  - QPS

### Components

| Component | Changes |
|-----------|----------|
| Router/EPP | Modality detection, architecture-aware plugins |
| vLLM-omni | New inference engine |
| Variant Autoscaler | Per-modality scaling |
| Observability | Modality metrics |
| Coordinator | Pipeline orchestration |
| Guides | Multimodal well-lit paths |

### APIs

| Endpoint | Description | Architecture |
|-----------|-------------|--------------|
| `/v1/chat/completions` | Text | Autoregressive |
| `/v1/audio/speech` | TTS | Omni / TTS |
| `/v1/audio/transcriptions` | STT | Whisper |
| `/v1/images/generations` | Image | Diffusion |
| `/v1/inference` | Custom routing API | Any |

### Dependencies

| Dependency | Status | Notes |
|------------|--------|------|
| Router/EPP | Existing | Extended |
| InferencePool | Existing | Reused |
| NIXL | Existing | GPU transfer |
| EC Connector | Existing | Cache transfer |
| MM-aware routing | Merged (#1531) | Foundation |
| Multimodal guide | In Progress (#1746) | Documentation |
| vLLM-omni | To Build | New engine |
| Variant Autoscaler | Existing | Extended |
| Prometheus/Grafana | Existing | Extended |

## Alternatives

- **Separate multimodal platform alongside llm-d.** Build a standalone system for non-text modalities. Ruled out because it duplicates infrastructure (gateway, routing, autoscaling) and forces operators to manage two independent stacks on the same GPU pools.

- **Modality-specific forks of llm-d.** Maintain separate llm-d forks tuned per modality (one for audio, one for image). Ruled out because it fragments the codebase, duplicates maintenance effort, and prevents shared improvements from benefiting all modalities.

- **HPA-only autoscaling instead of extending Variant Autoscaler.** Use generic Kubernetes HPA for multimodal workloads rather than teaching WVA about modality-specific metrics. Ruled out because HPA cannot reason about architecture-specific signals (denoising queue depth, encoder batching, cache pressure) that differ across model types.
