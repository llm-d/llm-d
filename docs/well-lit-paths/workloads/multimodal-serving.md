# Multimodal Workload Serving

Multimodal inputs are fundamentally changing the shape of production LLM traffic. 
A single prompt expands beyond text to include dense, non-text modalities—high-resolution images, video frames, or audio clips. 
Traditional HTTP requests are fast, uniform, and cheap. Standard round-robin request scheduling strategies balance this load well.
LLM requests break all three assumptions. Multimodal LLM requests (containing images, video, or audio) break them even further:

* **Massive Context Inflation** — A single high-resolution image, audio clip, or video file drastically inflates the context window (often by thousands of tokens).
* **Heavy Prefill Cost** — Running vision/auditory encoders and prefilling thousands of tokens is highly resource-intensive.

The **llm-d Router** extends text-based prefix scheduling across both aggregated and disaggregated inference architectures by tracking, hashing, and matching complex multimodal payloads across a distributed cluster.
The router intelligently directs incoming requests to the specific backend worker that already hold the corresponding pre-computed encoder cache and key-value (KV) blocks in memory.
Whether operating in a unified topology or a decoupled pencode-prefill-decode landscape, this targeted routing maximizes hardware efficiency and eliminates redundant processing.

---

## Deploy

### Multimodal Aggregated Guide

See the [multimodal aggregation guide](../../../guides/multimodal-serving/aggregation) for aggregated guide manifests and step-by-step deployment.

### Multimodal Disaggregated Guide

See the [multimodal e-disaggregation guide](../../../guides/multimodal-serving/e-disaggregation) for disaggregated guide manifests and step-by-step deployment.

---

## Architecture & Scheduling

The llm-d-router schedules multimodal requests using prefix cache affinity and server load metrics.

> [!NOTE]
> For the high-level scheduling architecture flow and EPP load-balancing diagrams, see the [Optimized Baseline guide](../foundations/optimized-baseline.md#architecture).

### Prefix-Aware Scheduling

#### Approximate Prefix-Cache Aware Routing

EPP maintains a view of each endpoints' prefix-cache state in memory including both text and multi-modality assets, and prioritizes routing an incoming request to an endpoint that has high prefix cache matching. Extending from its approximate prefix cache matching algorithm for text input, EPP mathematically estimates the virtual token footprint of each multimodal asset.

EPP uses two highly customizable **Token Estimation Strategies**:

##### Providing Asset Metadata

Both strategies need per-asset metadata (dimensions for images; duration, FPS, and resolution for video) to estimate an asset's token footprint. EPP resolves this metadata as follows:

* **Images** — When an image is embedded inline (raw bytes) in the request, EPP reads its width and height directly from the image content. When an image is supplied as a URL, EPP does **not** fetch the remote content and falls back to its configured defaults.
* **Video** — EPP cannot inspect video content directly, so clients should supply video metadata via request headers. Any header that is omitted falls back to the corresponding EPP default:
  * `x-llm-d-video-fps` — the video's frames per second.
  * `x-llm-d-video-duration-seconds` — the video's length in seconds.
  * `x-llm-d-video-resolution` — the video frame resolution as `WIDTHxHEIGHT` (e.g. `1280x720`).

##### A. Dimension-Based Approximation (e.g., Qwen-VL)

###### Image
Estimate tokens based on image width and height:
$$\text{Tokens} = \frac{\text{Image Width} \times \text{Image Height}}{\text{Factor}}$$

* The `Factor` parameter is configurable per-EPP:
  * For **Qwen 2.5 VL**: `factor = 784` (which is $28 \times 28$)
  * For **Qwen 3.5 VL**: `factor = 1024` (which is $32 \times 32$)

###### Video

Estimate tokens as the per-frame token count multiplied by the number of sampled frames:
$$\text{Tokens} = \text{tokensPerFrame} \times \text{Number of frames}$$

* **tokensPerFrame** — in `dynamic` mode this reuses the image formula ($\text{width} \times \text{height} / \text{factor}$).
* **Number of frames** — Qwen3-VL uses the **sampled** strategy: the video is sampled at `sampleFPS`, clamped to `[minFrames, maxFrames]`, then every `temporalPatchSize` sampled frames are merged into one token group:
$$\text{Number of frames} = \frac{\text{clamp}(\text{duration} \times \text{sampleFPS},\ \text{minFrames},\ \text{maxFrames})}{\text{temporalPatchSize}}$$

The total is capped by `maxVideoTokens`. Example per-EPP config for Qwen3-VL:

```yaml
estimate:
  video:
    tokensPerFrame:
      mode: dynamic
      dynamic:
        factor: 1024          # 32 × 32
    frames:
      mode: sampled
      minFrames: 4
      maxFrames: 768
      sampled:
        sampleFPS: 2
        temporalPatchSize: 2
    maxVideoTokens: 12288
```

##### B. Configuration-Based Fixed Allocation (e.g., Gemma 4)

Directly use fixed values from user configuration matching the model's support levels:

###### Image
* Gemma 4 supported values: 70, 140, 280 (default), 560, or 1120 tokens per image.

###### Video

Estimate tokens as the fixed per-frame allocation multiplied by the number of frames:
$$\text{Tokens} = \text{tokensPerFrame} \times \text{Number of frames}$$

* **tokensPerFrame** — a constant per-frame value taken directly from user configuration (`static` mode).
* **Number of frames** — Gemma 4 uses the **strided** strategy: frames are taken every `frameStride` source frames, clamped to `[minFrames, maxFrames]`:
$$\text{Number of frames} = \text{clamp}\left(\frac{\text{duration} \times \text{sourceFPS}}{\text{frameStride}},\ \text{minFrames},\ \text{maxFrames}\right)$$

Example per-EPP config for Gemma 4:

```yaml
estimate:
  video:
    tokensPerFrame:
      mode: static
      static:
        numTokensPerFrame: 296
    frames:
      mode: strided
      minFrames: 1
      maxFrames: 8
      strided:
        frameStride: 4
```

#### Precise Prefix-Cache Aware Routing

This routing strategy bases routing decisions directly on the precise, real-time physical memory block states of individual model server endpoints. It requires the router to tokenize the input, and subscribe to the KV-events channels of model server endpoints. Internally the router maintains an **indexer** which maintains a `block key → model server endpoints` mapping for every block resident across the fleet.
For incoming requests the router breaks the tokenized input into blocks and matches the block keys with the indexer to determine which model server endpoint has the longest prefix match. Multi-modal assets are converted to block keys considering the asset hash and size so that they are correctly accounted.

---

### Load-Aware Routing

EPP continuously probes each endpoints' metrics by scraping `/metrics` at a regular interval (50ms default). It scores endpoints on queue depth, running requests, and KV-cache utilization to schedule requests to the endpoint with the lowest load, avoiding hotspots caused by heterogeneous request patterns.

---

## Further Reading

* See [Optimized Baseline](../foundations/optimized-baseline.md) for details on text-based scheduling and general load-balancing.
* See [EPP Architecture](../../architecture/core/router/epp/README.md) for more details.
* See [KV-Cache Indexer](../../architecture/advanced/kv-management/kv-indexer.md) for details on precise event-driven indexing.
