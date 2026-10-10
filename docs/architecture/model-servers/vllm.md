# vLLM in llm-d

vLLM is the default model server engine in llm-d, delivering high-throughput LLM inference via PagedAttention, efficient continuous batching, and native integrations across llm-d's routing, caching, and topology layers.

## Pod Labeling & Discovery

Model server pods running vLLM join an [`InferencePool`](../router/inferencepool.md) through standard Kubernetes label matching. The Endpoint Picker (EPP) discovers and watches candidate pods matching the pool's selector:

```yaml
metadata:
  labels:
    llm-d.ai/engine-type: vllm # Default engine type; optional if vLLM is the sole backend
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: meta-llama-3-8b-instruct
    llm-d.ai/role: decode # Used in disaggregated topologies (prefill vs. decode)
```

The EPP uses `llm-d.ai/engine-type: vllm` to map Prometheus metric semantics and identify available engine capabilities.

## Router Telemetry & Metrics

The EPP continuously scrapes vLLM's Prometheus endpoint (default `GET /metrics` on port 8000) to populate real-time scheduling signals:

* `vllm:num_requests_waiting`: Queued requests waiting for GPU allocation (used by queue-based scorers).
* `vllm:num_requests_running`: Active requests currently undergoing execution in the engine.
* `vllm:kv_cache_usage_perc`: Percentage of GPU KV cache blocks currently allocated (used by saturation filters and flow control).
* `vllm:cache_config_info`: Metric exposing `block_size` and `num_gpu_blocks` labels required by prefix-cache scorers.
* `vllm:lora_requests_info`: Dynamic LoRA status reporting loaded and waiting adapter keys.

For detailed metric specifications and scraping configurations, see [Model Server Metrics](../../operations/observability/model-server-metrics.md#vllm).

## Prefix Caching & KV-Cache Events

vLLM supports Automatic Prefix Caching (APC) via the `--enable-prefix-caching` flag, allowing identical prompt prefixes across requests to reuse previously computed KV cache blocks.

In distributed clusters, vLLM can emit real-time cache state changes over ZeroMQ by configuring `--kv-events-config` with a `ZmqEventPublisher`. The [KV-Cache Indexer](../kv-management/kv-indexer.md) consumes these ZMQ events to build a global cluster-wide radix tree of cached prefixes, enabling [Precise Prefix-Cache Aware Routing](../../../guides/precise-prefix-cache-routing/README.md) in the EPP.

## Tiered KV Offloading & P2P Sharing

To scale effective KV capacity beyond GPU High Bandwidth Memory (HBM), vLLM integrates with multi-tier storage backends via `--kv-transfer-config`:

* **Native `OffloadingConnector`:** Moves inactive KV cache blocks to CPU system memory (`CPUOffloadingSpec`) or local NVMe storage.
* **`LMCacheConnector` & `MooncakeConnector`:** Integrates with distributed storage engines for high-speed host memory and disaggregated storage pooling.

See [KV Offloading](../kv-management/kv-offloader.md) and [P2P KV-Cache Sharing](../kv-management/p2p-kv-cache-sharing.md) for architecture details.

## Prefill/Decode Disaggregation

vLLM supports disaggregated serving where compute-bound prefill and memory-bandwidth-bound decode execute on dedicated pod groups:

* **Transfer Protocol:** Uses `NixlConnector` over RDMA / RoCE for high-bandwidth, kernel-bypass KV block transfers directly between prefill and decode GPU memory.
* **Handshake Coordination:** Model instances establish dynamic peer-to-peer ZeroMQ handshakes to coordinate memory allocations before transferring blocks.
* **Fallback Behavior:** If `do_remote_prefill` is omitted or KV loading fails, vLLM honors `kv_load_failure_policy=recompute` to fall back to local computation without dropping user requests.

For end-to-end design and operational recipes, see [Disaggregated Serving](../disaggregation/pd-disaggregation.md) and [vLLM Disaggregation Operations](../../operations/disaggregation/vllm.md).

## Wide Expert Parallelism (MoE)

For massive Mixture-of-Experts (MoE) models such as DeepSeek-V4, vLLM enables Wide Expert Parallelism across multi-node accelerator slices:

* Combines data-parallel attention (`--data-parallel-size`) with expert-parallel MLP layers (`--enable-expert-parallel`).
* Employs specialized all-to-all communication kernels (PPLX or DeepEP) for cross-node token dispatch and combination.

Learn more in [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md).

## Dynamic LoRA Serving

vLLM dynamically loads and offloads Parameter-Efficient Fine-Tuning (PEFT) LoRA adapters at runtime. When requests specify a LoRA adapter in the `model` parameter, vLLM dynamically allocates adapter weights onto GPU memory and reports adapter status via `vllm:lora_requests_info`, allowing the router to maximize LoRA affinity and minimize adapter thrashing.
