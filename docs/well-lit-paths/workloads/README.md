# Workloads

A workload guide provides the recommended, cohesive deployment for serving a production workload on llm-d. Each defines the workload, then composes the relevant [capability building blocks](../capabilities/README.md) into one stack tuned to serve it.

Where a well-lit path teaches a single feature, a workload guide starts from a use case and delivers the horizontal deployment that serves it best.

- **[Agentic Serving](agentic-serving.md)**: long, multi-turn, tool-using agentic programs (e.g. coding agents) — prefix-aware routing, KV-cache offloading, and P/D disaggregation composed for the agentic workload.
- **[Multimodal Serving](multimodal-serving.md)**: image / audio / video workloads — prefix- and load-aware routing that tracks and matches multimodal payloads across aggregated and disaggregated serving.
- **[Batch Serving](batch-serving/README.md)**: large-scale offline or asynchronous jobs — OpenAI-compatible batch gateway and lightweight async queue processors.

## Reinforcement Learning(RL) Integration
- **[verl Integration](rl/verl-integration.md)**: Use the [py-inference-scheduler](https://github.com/llm-d-incubation/py-inference-scheduler) for RL training rollouts(also called sampling) in [verl](https://github.com/volcengine/verl), replacing default routing with scored, metrics-aware scheduling across Ray-managed model-server actors. Providing flexible, and modular routing to support various sampling-intensive workloads.