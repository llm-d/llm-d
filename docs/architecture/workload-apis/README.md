# Workload APIs

Workload APIs in llm-d provide declarative Kubernetes controllers for orchestrating complex distributed AI workloads, spanning multi-node accelerator clusters and heterogeneous serving roles.

## Overview

Traditional Kubernetes workload primitives (such as Deployments and StatefulSets) manage individual, independent pods. However, distributed LLM serving topologies impose unique structural requirements:

* **Multi-Node Pod Groups:** Large models distributed via tensor, data, or expert parallelism require a leader coordinator and multiple worker pods that must be scheduled, placed, and restarted together as a single atomic unit.
* **Multi-Role Topologies:** Disaggregated architectures split serving into distinct prefill and decode stages that must be deployed with synchronized revisions, co-located within high-speed interconnect domains, and scaled in tandem as unified slices.

llm-d builds on two complementary Kubernetes SIG Apps APIs to address these challenges:

## Supported APIs

### [LeaderWorkerSet (LWS)](leaderworkerset.md)

[LeaderWorkerSet](leaderworkerset.md) introduces a pod group abstraction where each replica consists of a leader pod and one or more worker pods. Key capabilities include:

* **Hierarchical StatefulSets:** Composes two tiers of StatefulSets to provide deterministic ordinal ranks and predictable DNS names.
* **All-or-Nothing Restarts:** Restarts the complete group if any accelerator or pod fails, preventing distributed collective hangs.
* **Exclusive Placement & Gang Scheduling:** Integrates with cluster schedulers to co-locate pods on dedicated network fabrics and guarantee concurrent gang placement.

Learn more in the [LeaderWorkerSet deep dive](leaderworkerset.md).

### [DisaggregatedSet](disaggregatedset.md)

[DisaggregatedSet](disaggregatedset.md) orchestrates multi-role serving topologies (e.g., prefill and decode) across versioned slices. Key capabilities include:

* **Synchronized Slices:** Bundles complementary roles into unified, versioned replicas to eliminate contract mismatches across rollouts.
* **Placement Policies:** Enforces physical co-location of prefill and decode roles on the same network domain for low-latency RDMA KV transfer.
* **External Role Autoscaling:** Coordinates with autoscalers (such as KEDA) to scale individual roles independently while maintaining slice integrity.

Learn more in the [DisaggregatedSet deep dive](disaggregatedset.md).

## Comparison

| Feature | LeaderWorkerSet (LWS) | DisaggregatedSet |
| --- | --- | --- |
| **Primary Abstraction** | Homogeneous pod group (leader + workers) | Multi-role topology (e.g., prefill + decode) |
| **Workload Focus** | Multi-node tensor, data, or expert parallelism | Disaggregated serving architectures |
| **Rollout Unit** | Per-group pod rollout | Coordinated rollout across all roles |
| **Topology Domain** | Node-level or rack-level pod groups | Slice-level placement across interconnect domains |
| **Sub-Controllers** | Manages Pods directly | Generates and manages child `LeaderWorkerSet` resources |

## Operations

For operational runbooks, Kueue configuration, and rolling update strategies, see:

* [Operating the DisaggregatedSet](../../operations/disaggregation/disaggregatedset.md)
* [Disaggregated Serving Architecture](../disaggregation/pd-disaggregation.md)
* [Wide Expert Parallelism Architecture](../disaggregation/wide-expert-parallelism.md)
