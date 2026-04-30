# Model Servers

The model server is the component that runs inference on a model. llm-d supports vLLM and SGLang as model server backends.

## Functionality

A model server loads a model onto one or more accelerators (GPUs, TPUs, etc.) and exposes a supported API, such as OpenAI-compatible API, for inference requests. In the llm-d architecture, model servers are the compute layer -- they execute the actual prefill and decode steps that generate tokens.

Model servers are the lowest layer in the llm-d stack:

<p align="center">
  <img src="../../../assets/basic-architecture.svg" width="600" alt="Architecture">
</p>

Model servers are deployed independently from the rest of the llm-d stack. They join an `InferencePool` automatically via Kubernetes label selectors, and the EPP begins routing traffic to them once they are healthy.

Key responsibilities:

- **Serve inference requests** via an OpenAI-compatible API (`/v1/completions`, `/v1/chat/completions`)
- **Expose metrics** (KV-cache utilization, queue depth, active requests) that the EPP uses for intelligent scheduling
- **Manage KV-cache** on GPU memory, including prefix caching for repeated prompt prefixes
- **Support parallelism strategies** such as Tensor Parallelism (TP), Data Parallelism (DP), and Expert Parallelism (EP) for large models

## EPP <-> Model Server Protocol

This is the protocol between the EPP and the model servers. 

### Metrics Reporting

By default, the EPP is configured to scrape metrics from the model servers to make optimal request scheduling
decisions. In this mode of operation, the model servers MUST provide the following metrics via a Prometheus endpoint. The exact
metric names don't necessarily need to be the same as the recommended names here, however the
metric types and semantics MUST follow this doc.

| Metric | Type | Description | vLLM metric | Triton TensorRT-LLM| trtllm-serve | SGLang |
| ----- | ---- | ------------ | ---- | ---- | ---- | ---- |
| TotalQueuedRequests         | Gauge     | The current total number of requests in the queue.| `vllm:num_requests_waiting`| `nv_trt_llm_request_metrics{request_type=waiting}`| `trtllm_num_requests_waiting` | `sglang:num_queue_reqs`
| TotalRunningRequests         | Gauge     | The current total number of requests actively being served on the model server.| `vllm:num_requests_running`| `nv_trt_llm_request_metrics{request_type=scheduled}`| `trtllm_num_requests_running` | `sglang:num_running_reqs`
| KVCacheUtilization| Gauge     | The current KV cache utilization in percentage.| `vllm:kv_cache_usage_perc`| `nv_trt_llm_kv_cache_block_metrics{kv_cache_block_type=fraction}`| `trtllm_kv_cache_utilization` | `sglang:token_usage`
| [Optional] BlockSize         | Labeled/Gauge     | The block size in tokens to allocate memory, used by the prefix cache scorer. If this metric is not available, the BlockSize will be derived from the [prefix plugin config](https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/prefix-aware/#customize-the-prefix-cache-plugin).| name: `vllm:cache_config_info`, label name: `block_size`| `nv_trt_llm_kv_cache_block_metrics{kv_cache_block_type=tokens_per}` | `trtllm_kv_cache_tokens_per_block` | name: `sglang:cache_config_info`, label name: `page_size`
| [Optional] NumGPUBlocks| Labeled/Gauge     | The total number of blocks in the HBM KV cache, used by the prefix cache scorer. If this metric is not available, the NumGPUBlocks will be derived from the [prefix plugin config](https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/prefix-aware/#customize-the-prefix-cache-plugin).| name: `vllm:cache_config_info`, label name: `num_gpu_blocks`| `nv_trt_llm_kv_cache_block_metrics{kv_cache_block_type=max}` | `trtllm_kv_cache_max_blocks` | name: `sglang:cache_config_info`, label name: `num_pages`


To correctly map metrics names, model server Pods should be labeled with the model server type they are running as demonistrated below. Pods without the engine-type label will default to vLLM metrics names.


```yaml
metadata:
  labels:
    inference.networking.k8s.io/engine-type: vllm # other options: sglang, trtllm-serve, triton-tensorrt-llm 

```


### LoRA Adapter Serving

Model servers that support dynamic LoRA serving can benefit from the LoRA affinity algorithm. Note
the current algorithm in the reference EPP is highly biased towards vLLM's current dynamic LoRA 
implementation.

The model servers MUST support serving a LoRA adapter specified in the `model` argument of the
request, provided the requested adapter is valid.

The model server MUST expose the following LoRA adapter metrics via the same Prometheus endpoint:

* Metric name implemented in vLLM: `vllm:lora_requests_info` 
* Metric type: Gauge
* Metric value: The last updated timestamp (so the EPP can find the latest).
* Metric labels: 
  * `max_lora`: The maximum number of adapters that can be loaded to GPU memory to serve a batch.
  Requests will be queued if the model server has reached MaxActiveAdapter and cannot load the
  requested adapter. Example: `"max_lora": "8"`.
  * `running_lora_adapters`: A comma separated list of adapters that are currently loaded in GPU
    memory and ready to serve requests. Example: `"running_lora_adapters": "adapter1, adapter2"`
  * `waiting_lora_adapters`: A comma separated list of adapters that are waiting to be served. Example: `"waiting_lora_adapters": "adapter1, adapter2"`

### Prefix Cache Reuse

The EPP supports prefix cache optimized request scheduling. To benefit from the optimal prefix aware request scheduling, model servers SHOULD support prefix cache reuse, such as the [vllm automatic prefix caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html) feature.


### Health Checks

Model servers are expected to expose health endpoints that Kubernetes uses for liveness and readiness probes:

- **Liveness**: `GET /health` -- confirms the server process is alive
- **Readiness**: `GET /health` -- confirms the server is ready to accept requests

## Further Reading

- [vLLM Documentation](https://docs.vllm.ai/)
- [SGLang Documentation](https://github.com/sgl-project/sglang)
- [InferencePool](inferencepool.md) -- how model servers are discovered and managed
- [EPP](epp) -- how the router routes requests to model servers informed by model servers metrics
