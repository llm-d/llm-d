# Multimodal Serving in llm-d

Multimodal models (such as `Qwen/Qwen3-VL-32B-Instruct`) process combinations of text and media (images, video, or audio). Serving these models introduces unique computational bottlenecks because extracting feature embeddings from high-resolution images or videos is extremely compute-intensive compared to standard text token processing.

`llm-d` supports two distinct architectural patterns for serving multimodal models:

1. **Aggregated Serving (Aggregation)**: Standard deployments where the entire inference lifecycle (multimodal encode, prefill, and decode) is executed on the same model server replica.
2. **Encode-Disaggregated Serving (E-Disaggregation)**: Advanced topologies where the heavy multimodal encoding phase is offloaded to specialized, dedicated worker pools.

---

## Guide Index

* **[Aggregated Serving (Aggregation) Guide](./aggregation/README.md)**: Deploy a unified serving topology with prefix-cache and load-aware routing that tracks and matches multimodal payloads across model servers.
* **[Encode-Disaggregated Serving (E-Disaggregation) Guide](./e-disaggregation/README.md)**: Deploy specialized multi-tier topologies (**E/PD** or **E/P/D**) to offload vision encoding to dedicated nodes and transfer embeddings over high-performance NIXL dataplanes.

---

## Understanding the Difference

### 1. Aggregated Serving (Aggregation)

In an **aggregated** setup, every model server instance (or replica) is homogeneous and runs the full model engine. When a request arrives with an image, the same GPU replica processes:
1. **Encode**: Converts the image into visual embeddings using the model's Vision Transformer (ViT) component.
2. **Prefill**: Processes the visual embeddings and user prompt text tokens to compute KV caches.
3. **Decode**: Generates output text tokens sequentially.

To optimize this path, the `llm-d Router` (EPP) performs **Prefix-Cache Aware** and **Load-Aware Routing**. By hashing both the text prompt and the visual assets (images), the EPP directs requests to the specific replicas that are likely to already have cached KV tensors or processed inputs, minimizing redundant compute.

### 2. Encode-Disaggregated Serving (E-Disaggregation)

**Encode Disaggregation** physically decouples the heavy encoder (e.g., Vision Transformer) from the rest of the text generation pipeline.

It introduces dedicated **Encode (E) Workers** that only run the encoder part of the model. The downstream workers (**PD** or separated **P** and **D** workers) only process the text tokens and the pre-computed embeddings. 

* **How it works**:
  1. The client sends a multimodal request.
  2. The llm-d Router (EPP) intercepts the request and assigns an **Encode Worker** to handle the media processing.
  3. The request metadata is routed to the selected downstream worker (e.g., Prefill or Decode).
  4. The downstream worker pulls the computed embeddings directly from the Encode Worker via the **EC Connector** (utilizing a high-performance **NIXL** data plane for direct memory transfer and **ZMQ** for control signals).
  5. The downstream worker performs text generation without needing to process the visual inputs locally.

#### Supported Topologies
* **E/PD**: Simple disaggregation. It has dedicated Encode workers and combined Prefill/Decode workers.
* **E/P/D**: Full three-stage pipeline. Dedicated Encode workers, dedicated Prefill workers, and dedicated Decode workers. It inherits the benefits of [Prefill/Decode Disaggregation](../pd-disaggregation/README.md) while scaling vision encoding separately.

---

## Comparative Analysis

The table below contrasts Aggregated Serving against Encode-Disaggregated Serving across key dimensions:

| Dimension | Aggregated Serving (Aggregation)                                                             | Encode-Disaggregated Serving (E-Disaggregation)                                                                  |
| :--- |:---------------------------------------------------------------------------------------------|:-----------------------------------------------------------------------------------------------------------------|
| **Worker Roles** | Homogeneous. Every pod runs the entire model (Encode + Prefill + Decode).                    | Heterogeneous. Dedicated Encode pods + downstream PD or P/D pods.                                                |
| **Vision Encoding Location** | Locally on the assigned model server pod.                                                    | Offloaded to a dedicated Encode worker pool.                                                                     |
| **Data Transfer Overhead** | **None**. All computations happen locally within the GPU memory.                             | **Low-Medium**. Precomputed embeddings must be transferred over the network via **EC Connector**.                |
| **Parallelism for Multi-Media Requests** | Limited. Multiple images in a single request are processed sequentially on the same replica. | **High**. Multiple images or video frames can be processed in parallel across multiple Encode workers.           |
| **Scaling & Specialization** | Suboptimal. Cannot scale encode resources independently from LLM text generation resources.  | **Optimal**. Encode workers can be scaled and allocated hardware (e.g., smaller GPUs or CPUs) independently.     |
| **Deployment Complexity** | **Low**. Standard Single-deployment manifest and simple router configuration.                | **High**. Multiple deployment tiers, sidecars, NIXL network overlays, and specialized routing rules.             |
| **Workload Profile Suitability** | Best for lightweight media inputs and smaller models.                                        | Best for heavy media processing (video, audio, multiple high-res images) with high concurrency and large models. |

---

## When to Choose Which?

### Choose Aggregated Serving (Aggregation) if:
* Your multimodal inputs are relatively small (e.g., low-resolution images).
* Your model is small.
* You prefer lower deployment complexity and do not want to configure multi-tier networking (NIXL/ZMQ) across different pods.
* You already have a strong prefix-cache hit rate, which mitigates redundant encoding.

### Choose Encode-Disaggregated Serving (E-Disaggregation) if:
* Your requests frequently contain **large or multiple media assets** (e.g., document parsing with dozens of images, high-definition videos, or long audio tracks).
* Your model is large.
* The Vision Encoder is extremely heavy, and running it on standard text generation pods stalls the sequential decoding phase.
* You want to scale the encoding tier independently (e.g., using GPUs optimized for vision tasks and separate GPUs/TPUs specialized for text generation).
* You want to process multiple media inputs within a single request **concurrently** across different nodes.

---
