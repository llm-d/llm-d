# Well-Lit Paths

A **well-lit path** is a documented, tested, and benchmarked deployment pattern for large scale LLM serving.

These paths are targeted at production serving that want to achieve SOTA performance while minimizing operational complexity. The well-lit paths help identify those key optimizations, understand their tradeoffs, and verify the gains against your own workload.

We focus on the following use cases:
* Deploying a self-hosted LLM behind a single workload across tens or hundreds of nodes
* Running a production model-as-a-service platform that supports many users and workloads sharing one or more LLM deployments

## Paths

We offer the following well-lit paths:

* [**Intelligent Inference Scheduling**](./intelligent-inference-scheduling/README.md) -- Deploy a model server with an LLM-aware proxy to decrease latency and increase throughput via prefix-cache aware routing and customizable scheduling policies.
* [**Prefill/Decode Disaggregation**](./prefill-decode-disaggregation.md) -- Improve throughput per GPU and quality of service by splitting inference into prefill and decode servers, primarily for medium-large models with long prompts.
* [**Wide Expert Parallelism**](./wide-expert-parallelism.md) -- Deploy very large Mixture-of-Experts (MoE) models like DeepSeek-R1 with Data Parallelism and Expert Parallelism to maximize KV cache space and overall throughput.
* [**Tiered Prefix Cache**](./tiered-prefix-cache.md) -- Increase prefix cache reuse for long context or high concurrency workloads by adding tiered prefix cache (e.g., offloading to CPU memory) beyond accelerator memory.
* [**Workload Autoscaling**](./workload-autoscaling.md) -- Scale model server deployments using inference-specific signals (queue depth, KV-cache utilization, running requests) instead of GPU utilization, with cost-optimized variant selection and scale-to-zero support.

## Composability

Well-lit paths compose. Each path is a deployment primitive that can be combined with others:

- **Intelligent Scheduling** -- the foundation, always active, composes with every other path
- **P/D Disaggregation + Wide EP** -- the recommended configuration for frontier MoE models
- **Tiered Prefix Cache** -- layers onto any path to extend effective cache capacity
- **Workload Autoscaling** -- adds elastic scaling to any deployment

| Combination | Status |
|---|---|
| Scheduling + Tiered Cache | Validated |
| Scheduling + P/D Disaggregation | Validated |
| Scheduling + Wide EP | Validated |
| Scheduling + Autoscaling | Validated |
| P/D Disaggregation + Wide EP | Validated |
| Tiered Cache + P/D Disaggregation | In progress |
| Autoscaling + P/D Disaggregation | Planned |

## Deployment

Each page in this section covers what a path is, when to use it, key decisions, and operational guidance. For step-by-step deployment instructions, see the [deployment guides](https://github.com/llm-d/llm-d/tree/main/guides).

> [!IMPORTANT]
> The deployment guides are intended to be a starting point for your own configuration and deployment of model servers.
