# Well-Lit Paths

A **well-lit path** is a documented, tested, and benchmarked deployment pattern for large scale LLM serving.

These paths are targeted at production serving that want to achieve SOTA performance while minimizing operational complexity. The well-lit paths help identify those key optimizations, understand their tradeoffs, and verify the gains against your own workload.

Each guide is hosted in [llm-d guides](https://github.com/llm-d/llm-d/tree/main/guides).

## Paths

* [**Intelligent Inference Scheduling**](./intelligent-inference-scheduling.md) -- vLLM-aware load-balancing enables smarter request routing that improves SLOs.
* [**P/D Disaggregation**](./pd-disaggregation.md) -- llm-d composes vLLM and Inference Scheduler to enable P/D disaggregation.
* [**KV Cache Management**](./kv-cache-management.md) -- Leverage all system resources to maximize prefix cache hit rate within the cluster.
* [**Multi-Node Wide Expert Parallelism**](./wide-expert-parallelism.md) -- llm-d's Kubernetes-native design composes the DP/EP implementation with the rest of the llm-d system.
* [**Flow Control**](./flow-control.md) -- Flow control multiplexing of different request classes onto the same model deployment.

> [!IMPORTANT]
> The deployment guides are intended to be a starting point for your own configuration and deployment of model servers.
