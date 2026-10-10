# Disaggregation

Disaggregation in llm-d decouples distinct phases and layers of large language model serving across specialized compute instances and accelerator slices, optimizing resource utilization and latency.

## Overview

Modern LLM inference presents asymmetric computational requirements:

* **Prefill vs. Decode:** The initial prompt evaluation (prefill) is compute- and FLOPs-bound, while token generation (decode) is memory-bandwidth-bound. Running both phases on the same accelerator leads to pipeline interference and head-of-line blocking.
* **Dense Attention vs. Sparse Experts:** For Mixture-of-Experts (MoE) architectures, attention operations scale efficiently with data parallelism, whereas expert feed-forward layers require expert parallelism across accelerator memory.

llm-d addresses these challenges through two complementary architectural strategies:

### [Disaggregated Serving](pd-disaggregation.md)

Separates prefill instances (`role=prefill`) from decode instances (`role=decode`), coordinating request flow through the Endpoint Picker (EPP) and transferring KV-cache blocks over high-speed RDMA networks via NIXL.

See the [Disaggregated Serving deep dive](pd-disaggregation.md) for request flow orchestration, routing proxy sidecar mechanics, and NIXL transport integration.

### [Wide Expert Parallelism](wide-expert-parallelism.md)

Scales massive MoE models across multi-node GPU/accelerator clusters by combining data-parallel (DP) attention with expert-parallel (EP) MLP layers, avoiding KV-cache replication while enabling sparse all-to-all token dispatch and combination.

See the [Wide Expert Parallelism deep dive](wide-expert-parallelism.md) for DP/EP execution flow and DP-aware routing integration.

## Topology & Operations

Disaggregated and wide-EP deployments rely on specialized workload controllers and day-2 operations:

* **[Workload APIs](../workload-apis/README.md):** Declaratively manage multi-node pod groups and versioned serving slices using [LeaderWorkerSet](../workload-apis/leaderworkerset.md) and [DisaggregatedSet](../workload-apis/disaggregatedset.md).
* **[Operate Disaggregated Serving](../../operations/disaggregation/README.md):** Operational guides for DisaggregatedSet rollouts, vLLM NIXL setup, and SGLang bootstrap coordination.
