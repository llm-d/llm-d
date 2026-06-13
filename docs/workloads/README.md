# Workloads

A workload guide provides the recommended, cohesive deployment for serving a production workload on llm-d. Each defines the workload, then composes the relevant [well-lit paths](../well-lit-paths/README.md) — the capability building blocks — into one stack tuned to serve it.

Where a well-lit path teaches a single feature, a workload guide starts from a use case and delivers the horizontal deployment that serves it best.

- **[Agentic Serving](agentic-serving.md)**: long, multi-turn, tool-using agentic programs (e.g. coding agents) — prefix-aware routing, KV-cache offloading, and P/D disaggregation composed for the agentic workload.
- **[Multimodal Serving](multimodal-serving.md)**: image / audio / video workloads — prefix- and load-aware routing that tracks and matches multimodal payloads across aggregated and disaggregated serving.
