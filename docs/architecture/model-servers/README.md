# Model Servers

Model servers are the compute layer in the llm-d stack: they load model weights onto hardware accelerators (GPUs, TPUs, XPUs), execute the prefill and decode forward passes, manage the local KV cache, and expose an OpenAI-compatible HTTP API (`/v1/completions`, `/v1/chat/completions`), health probes (`GET /health`), and Prometheus metrics that the [Endpoint Picker (EPP)](../router/epp.md) scrapes for scheduling.

Model servers are deployed independently from the router and join an [InferencePool](../router/inferencepool.md) automatically via Kubernetes pod label selectors.

## Supported Model Servers

- **[vLLM](vllm.md)**: Default model server engine (`llm-d.ai/engine-type: vllm`) featuring PagedAttention, continuous batching, dynamic LoRA adapter serving, ZMQ KV-cache event publishing, native/LMCache/Mooncake tiered KV offloading, `NixlConnector` RDMA for P/D disaggregation, and wide expert parallelism.
- **[SGLang](sglang.md)**: High-performance model server engine (`llm-d.ai/engine-type: sglang`) featuring RadixAttention prefix caching, ZMQ KV-cache event publishing, HiCache hierarchical KV caching, prefill bootstrap server coordination for P/D disaggregation, and DeepEP wide expert parallelism.
- **TensorRT-LLM (`trtllm-serve`)**: NVIDIA's inference server (`llm-d.ai/engine-type: trtllm-serve`). Exposes EPP load and KV-cache gauges (`trtllm_num_requests_waiting`, `trtllm_num_requests_running`, `trtllm_kv_cache_utilization`, `trtllm_kv_cache_tokens_per_block`, `trtllm_kv_cache_max_blocks`) at `/prometheus/metrics` when started with both `return_perf_metrics: true` and `enable_iter_perf_stats: true` via `--extra_llm_api_options` (requires TensorRT-LLM v1.3.0rc12+; see the [Optimized Baseline guide](../../../guides/optimized-baseline/README.md)).
