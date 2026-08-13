# Disaggregated Serving Operations

Disaggregated serving separates the **prefill** and **decode** stages of LLM inference onto different model server instances. While disaggregated serving offers superior performance and resource efficiency, it introduces operational complexity around dynamic connections, request cancellation, fault tolerance, and safe rollouts.

For an overview of the architecture, request flow orchestration, and KV cache transfer fundamentals, see the [Disaggregated Serving Concepts](../../architecture/advanced/disaggregation/README.md) page.

## Engine Operations Guides

Operational behavior differs by model server engine:

* **[vLLM Operations](./vllm.md)** — Dynamic connections via NIXL peer-to-peer handshake, lease-based KV block management, fault tolerance policies (`fail` vs `recompute`), and rollout compatibility.
* **[SGLang Operations](./sglang.md)** — Lazy connection establishment via prefill bootstrap server, heartbeat-based fault isolation, waiting timeouts, and rollout compatibility.
