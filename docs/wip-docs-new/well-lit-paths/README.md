# Well-Lit Paths

A **well-lit path** is a documented, tested, and benchmarked deployment pattern for large scale LLM serving.

These paths are targeted at production serving that want to achieve SOTA performance while minimizing operational complexity. The well-lit paths help identify those key optimizations, understand their tradeoffs, and verify the gains against your own workload.

Each guide is hosted in [llm-d guides](https://github.com/llm-d/llm-d/tree/main/guides).

## Paths

* [**Intelligent Inference Scheduling**](./intelligent-inference-scheduling.md) -- Prefix-cache and load-aware request scheduling.
* [**Flow Control**](./flow-control.md) -- Prioritize traffic from multiple tenants on the same server resources.
* [**P/D Disaggregation**](./pd-disaggregation.md) -- Separate prefill and decode phases of inference into separate instances.
* [**Multi-Node Wide Expert Parallelism**](./wide-expert-parallelism.md) -- Deploy large MoE models over multiple nodes with DP/EP.
* [**KV Cache Management**](./kv-cache-management.md) -- Offload KV caches to CPU RAM and storage for increased cache hit rates.

### Experimental
* [**Predicted Latency Scheduling**](./experimental/predicted-latency.md) -- Expand on intelligent inference scheduling with ML-model based prediction.

> [!IMPORTANT]
> The deployment guides are intended to be a starting point for your own configuration and deployment of model servers.
