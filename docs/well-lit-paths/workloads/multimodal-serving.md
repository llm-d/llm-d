# Multimodal Workload Serving

Traditional HTTP requests are fast, uniform, and cheap. Standard round-robin request scheduling strategies balance this load well.

LLM requests break all three assumptions. Multimodal LLM requests (containing images, video, or audio) break them even further:

* **Context Inflation** — A single high-resolution image, audio clip, or video file drastically inflates the context window (often by thousands of tokens).
* **Heavy Prefill Cost** — Running vision/auditory encoders and prefilling thousands of tokens is highly resource-intensive.

The **llm-d Router** extends text-based prefix scheduling across both aggregated and disaggregated inference architectures by tracking, hashing, and matching complex multimodal payloads across a distributed cluster.
The router intelligently directs incoming requests to the specific backend worker that already hold the corresponding pre-computed encoder cache and key-value (KV) blocks in memory.
Whether operating in a unified topology or a decoupled pencode-prefill-decode landscape, this targeted routing maximizes hardware efficiency and eliminates redundant processing.

---

## Deploy

### Multimodal Aggregated Guide

See the [multimodal optimized baseline guide](../../guides/multimodal-serving/optimized-baseline) for aggregated guide manifests and step-by-step deployment.

### Multimodal Disaggregated Guide

See the [multimodal e-disaggregation guide](../../guides/multimodal-serving/e-disaggregation) for disaggregated guide manifests and step-by-step deployment.

---

## Architecture & Scheduling

The llm-d-router schedules multimodal requests using prefix cache affinity and server load metrics.

> [!NOTE]
> For the high-level scheduling architecture flow and EPP load-balancing diagrams, see the [Optimized Baseline guide](../well-lit-paths/optimized-baseline.md#architecture).

### Prefix-Aware Scheduling

EPP maintains a view of each endpoints' prefix-cache state. When a request arrives, it identifies which pod already holds the matching prefix in KV-cache and routes the request there.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../assets/prefix-aware-routing.svg" alt="Prefix-Aware Routing">
  </picture>
</p>

#### Approximate Prefix-Cache Aware Routing

EPP maintains a view of each endpoints' prefix-cache state in memory including both text and multi-modality assets, and prioritizes routing an incoming request to an endpoint that has high prefix cache matching. Extending from its approximate prefix cache matching algorithm for text input, EPP mathematically estimates the virtual token footprint of each multimodal asset.

EPP uses two highly customizable **Token Estimation Strategies**:

##### A. Dimension-Based Approximation (e.g., Qwen-VL)

Estimate tokens based on image width and height:
$$\text{Tokens} = \frac{\text{Image Width} \times \text{Image Height}}{\text{Factor}}$$

* The `Factor` parameter is configurable per-EPP:
  * For **Qwen 2.5 VL**: `factor = 784` (which is $28 \times 28$)
  * For **Qwen 3.5 VL**: `factor = 1024` (which is $32 \times 32$)

##### B. Configuration-Based Fixed Allocation (e.g., Gemma 4)

Directly use fixed values from user configuration matching the model's support levels:

* Gemma 4 supported values: 70, 140, 280 (default), 560, or 1120 tokens per image.

#### Precise Prefix-Cache Aware Routing

This routing strategy bases routing decisions directly on the precise, real-time physical memory block states of individual model server endpoints. It requires the router to tokenize the input, and subscribe to the KV-events channels of model server endpoints. Internally the router maintains an **indexer** which maintains a `block key → model server endpoints` mapping for every block resident across the fleet.
For incoming requests the router breaks the tokenized input into blocks and matches the block keys with the indexer to determine which model server endpoint has the longest prefix match. Multi-modal assets are converted to block keys considering the asset hash and size so that they are correctly accounted.

---

### Load-Aware Routing

EPP continuously probes each endpoints' metrics by scraping `/metrics` at a regular interval (50ms default). It scores endpoints on queue depth, running requests, and KV-cache utilization to schedule requests to the endpoint with the lowest load, avoiding hotspots caused by heterogeneous request patterns.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../assets/load-aware-routing.svg" alt="Load-Aware Routing">
  </picture>
</p>

---

## Further Reading

* See [Optimized Baseline](../well-lit-paths/optimized-baseline.md) for details on text-based scheduling and general load-balancing.
* See [EPP Architecture](../architecture/core/router/epp/README.md) for more details.
* See [KV-Cache Indexer](../advanced/kv-management/kv-indexer.md) for details on precise event-driven indexing.
