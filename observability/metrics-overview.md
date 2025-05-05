# 📊 Metrics Overview

llm-d provides comprehensive observability for distributed LLM inference, exposing detailed metrics from both the **Prefill/Decode Pods** (vLLM) and the **Endpoint Picker Pod** (inference-gateway). These metrics are available via Prometheus and can be visualized in Grafana dashboards.

Below is an overview of the key metrics you can monitor to understand and optimize your system’s performance:

---

## vLLM Metrics (Prefill & Decode Pods)

| Metric | Type | Description |
|--------|------|-------------|
| **vllm:cpu_prefix_cache_hit_rate** | Gauge | Ratio of cache hits for prefix tokens, indicating KV cache efficiency. |
| **vllm:prompt_tokens_total** | Counter | Total number of prompt tokens processed. |
| **vllm:generation_tokens_total** | Counter | Total number of generation tokens produced. |
| **vllm:request_success_total** | Counter | Total number of successful requests. |
| **vllm:request_prompt_tokens** | Histogram | Distribution of prompt tokens per request. |
| **vllm:request_generation_tokens** | Histogram | Distribution of generation tokens per request. |
| **vllm:time_to_first_token_seconds** | Histogram | Time from request start to first output token (TTFT). |
| **vllm:time_per_output_token_seconds** | Histogram | Latency per output token. |
| **vllm:e2e_request_latency_seconds** | Histogram | End-to-end request latency. |
| **vllm:request_queue_time_seconds** | Histogram | Time spent in the request queue. |
| **vllm:request_inference_time_seconds** | Histogram | Time spent on inference computation. |
| **vllm:request_prefill_time_seconds** | Histogram | Time spent in the prefill stage. |
| **vllm:request_decode_time_seconds** | Histogram | Time spent in the decode stage. |
| **vllm:request_max_num_generation_tokens** | Histogram | Distribution of max generation tokens per request. |
| **vllm:num_preemptions_total** | Counter | Number of request preemptions. |
| **vllm:cache_config_info** | Gauge | KV cache configuration details. |
| **vllm:lora_requests_info** | Gauge | LoRA (Low-Rank Adaptation) request info. |
| **vllm:tokens_total** | Counter | Total tokens processed. |
| **vllm:iteration_tokens_total** | Histogram | Distribution of tokens per iteration. |
| **vllm:time_in_queue_requests** | Histogram | Time requests spend in the queue. |
| **vllm:model_forward_time_milliseconds** | Histogram | Model forward pass time. |
| **vllm:model_execute_time_milliseconds** | Histogram | Model execution time. |
| **vllm:request_params_n** | Histogram | Distribution of request parameter n. |
| **vllm:request_params_max_tokens** | Histogram | Distribution of max tokens parameter. |
| **vllm:spec_decode_draft_acceptance_rate** | Gauge | Draft acceptance rate in speculative decoding. |
| **vllm:spec_decode_efficiency** | Gauge | Efficiency of speculative decoding. |
| **vllm:spec_decode_num_accepted_tokens_total** | Counter | Number of accepted tokens in speculative decoding. |
| **vllm:spec_decode_num_draft_tokens_total** | Counter | Number of draft tokens in speculative decoding. |
| **vllm:spec_decode_num_emitted_tokens_total** | Counter | Number of emitted tokens in speculative decoding. |

---

## Inference-Gateway Metrics (Endpoint Picker Pod)

| Metric | Type | Description |
|--------|------|-------------|
| **inference_model_request_total** | Counter | Total number of requests per model. |
| **inference_model_request_error_total** | Counter | Total number of request errors per model. |
| **inference_model_request_duration_seconds** | Distribution | Distribution of response latency per model. |
| **normalized_time_per_output_token_seconds** | Distribution | Response latency per output token. |
| **inference_model_request_sizes** | Distribution | Distribution of request sizes (bytes). |
| **inference_model_response_sizes** | Distribution | Distribution of response sizes (bytes). |
| **inference_model_input_tokens** | Distribution | Distribution of input token counts. |
| **inference_model_output_tokens** | Distribution | Distribution of output token counts. |
| **inference_model_running_requests** | Gauge | Number of running requests per model. |
| **inference_pool_average_kv_cache_utilization** | Gauge | Average KV cache utilization for the inference pool. |
| **inference_pool_average_queue_size** | Gauge | Average number of requests pending in the server queue. |
| **inference_pool_per_pod_queue_size** | Gauge | Total queue size per model server pod. |
| **inference_pool_ready_pods** | Gauge | Number of ready pods in the inference pool. |
| **inference_extension_info** | Gauge | General information about the current build. |

---

## Example Metrics Visualizations

- **Token Throughput:** Track input/output token rates to monitor LLM utilization.
- **Request Latency:** Visualize end-to-end and per-stage latencies (prefill, decode, inference).
- **Queue Times:** Identify bottlenecks by monitoring queue durations.
- **Cache Utilization:** Optimize performance by tracking KV cache hit rates and utilization.
- **Request/Response Sizes:** Understand workload characteristics and optimize resource allocation.
- **Error Rates:** Quickly spot issues by monitoring error counters.

---

> Reference: [vllm metrics documentation](https://docs.vllm.ai/en/v0.8.2/design/v1/metrics.html).
> Reference: [gateway-api-inference-extension metrics documentation](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics/#exposed-metrics)

