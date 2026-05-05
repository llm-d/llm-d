# P/D Disaggregation

LLM inference has two computationally distinct phases:
* **Prefill** processes the entire input prompt in a single forward pass - it is compute-bound, bottlenecked by the GPU flops available.
* **Decode** generates output tokens one at a time from the KV-cache - it is memory-bandwidth-bound, bottlenecked by how fast data moves from HBM to on-chip memory.

For long context workloads (10:1 ISL:OSL) and medium-to-large models, separating prefill and decode into separate instances enables:
* Improved throughput via specialization or prefill and decode
* Improved quality of service, as long context prefills will not block decode work

llm-d's EPP natively supports the concept of disaggregation, enabling composition with other scorers (e.g. prefix-aware routing).

> [!IMPORTANT]
> NIXL supports TCP transfer, but high-bandwidth networking
> (IB, RoCE, EFA) is **highly recommended** for production usage.

## Deploy

See the [P/D Disaggregation guide](https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation) for manifests and step-by-step deployment.

## Architecture

![P/D Disaggregation](../assets/pd-disaggregation.svg)

The setup creates 2 `Deployments` of vLLM (all are part of the same `InferencePool`):
- The **prefill** `Deployment` is 4 replicas of TP=1 vLLM - labeled with `llm-d.ai/role=prefill`.
- The **decode** `Deployment` is 1 replica of TP=5 vLLM - labeled with `llm-d.ai/role=decode`. All these pods have a routing proxy sidecar.

During the standard request flow:
- Request arrives at the proxy, which forwards the request to the EPP
- EPP schedules the request with P/D disaggregation, using the labels to detect the decode and prefill variants
- Request is routed to the sidecar, which forwards the request to the prefill instance
- Prefill instance processes the prompt, returning metadata about how to retrieve the KV blocks
- Decode instance pulls the KVs over RDMA (IB, RoCE, EFA) with NIXL
- Decode instances processes the decodes

## Further Reading

See [PD Architecture](../architecture/advanced/disaggregation/README.md) for more details.