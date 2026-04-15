# Well-Lit Paths

A **well-lit path** is a documented, tested, and benchmarked deployment pattern for large scale LLM serving.

These paths are targeted at production serving that want to achieve SOTA performance while minimizing operational complexity. The well-lit paths help identify those key optimizations, understand their tradeoffs, and verify the gains against your own workload.

Each guide is hosted in [llm-d guides](https://github.com/llm-d/llm-d/tree/main/guides).

## Paths

We offer the following well-lit paths:

* [**Intelligent Inference Scheduling**](./intelligent-inference-scheduling/README.md) -- Deploy a model server with an LLM-aware proxy to decrease latency and increase throughput via prefix-cache aware routing and customizable scheduling policies.
* [**Prefill/Decode Disaggregation**](./prefill-decode-disaggregation.md) -- Improve throughput per GPU and quality of service by splitting inference into prefill and decode servers, primarily for medium-large models with long prompts.
* [**Wide Expert Parallelism**](./wide-expert-parallelism.md) -- Deploy very large Mixture-of-Experts (MoE) models like DeepSeek-R1 with Data Parallelism and Expert Parallelism to maximize KV cache space and overall throughput.
* [**Tiered Prefix Cache**](./tiered-prefix-cache.md) -- Increase prefix cache reuse for long context or high concurrency workloads by adding tiered prefix cache (e.g., offloading to CPU memory) beyond accelerator memory.
* [**Workload Autoscaling**](./workload-autoscaling.md) -- Scale model server deployments using inference-specific signals (queue depth, KV-cache utilization, running requests) instead of GPU utilization, with cost-optimized variant selection and scale-to-zero support.

> [!IMPORTANT]
> The deployment guides are intended to be a starting point for your own configuration and deployment of model servers.
