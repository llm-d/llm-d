# DisaggregatedSet

[DisaggregatedSet](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) is a Kubernetes SIG Apps API designed to orchestrate disaggregated serving topologies where an inference workload consists of multiple specialized roles (such as prefill and decode) that scale, update, and co-locate as unified slices.

In `llm-d`, DisaggregatedSet manages the coordinated lifecycle of prefill/decode disaggregation ([Disaggregated Serving](../disaggregation/pd-disaggregation.md)) and wide expert parallelism ([Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md)). It synchronizes revisions across roles, replicates complete topologies into slices, and works in tandem with the llm-d Router's rollout screener to prevent cross-revision request pairing during rolling updates. See [Disaggregated Serving Operations](../../operations/disaggregation/disaggregatedset.md) for deployment and operational details.

> [!NOTE]
> This page is an in-repo reference overview for `llm-d`. Full documentation is mirrored on `llm-d.ai` from the upstream [LWS DisaggregatedSet Documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Architecture: Two Complementary APIs

DisaggregatedSet and LeaderWorkerSet represent two complementary abstraction levels in distributed model serving. LeaderWorkerSet focuses on coordinating homogeneous multi-node pod groups within a single role or replica, whereas DisaggregatedSet coordinates multi-role heterogeneous groups into co-located, versioned topologies. See the upstream [DisaggregatedSet Architecture documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Comparison Matrix

The table below contrasts the responsibilities and operational semantics of `LeaderWorkerSet` and `DisaggregatedSet`:

| Feature | LeaderWorkerSet (LWS) | DisaggregatedSet |
| --- | --- | --- |
| Primary Abstraction | Homogeneous pod group (leader + workers) | Multi-role topology (e.g., prefill + decode) |
| Workload Focus | Multi-node tensor, data, or expert parallelism | Disaggregated serving architectures |
| Rollout Unit | Per-group pod rollout | Coordinated rollout across all roles |
| Topology Domain | Node-level or rack-level pod groups | Slice-level placement across interconnect domains |
| Sub-Controllers | Manages Pods directly | Generates and manages child `LeaderWorkerSet` resources |

## When to Use Which API

Use `LeaderWorkerSet` directly when deploying single-role workloads that span multiple nodes or require rank assignment, such as standalone wide-EP or multi-node tensor-parallel inference. Use `DisaggregatedSet` when deploying disaggregated prefill/decode architectures where both roles must be managed with unified revisions, coordinated scaling, and slice-level network locality. See the upstream [Selection Guide](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Relationship to LeaderWorkerSet

DisaggregatedSet acts as a higher-level supervisor that creates and manages underlying `LeaderWorkerSet` resources for each role in each slice. Every generated resource is named `<ds>-<slice>-<revision>-<role>`, inheriting the lifecycle and exclusive placement properties of LWS while decoupling role definitions. See the upstream [LWS Relationship documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Key Design Principles

DisaggregatedSet is built on principles of declarative role definition, slice isolation, versioned coordination, and failure domain preservation. By treating the multi-role topology as a single versioned contract, it eliminates mismatched API contracts or network configurations during rollout. See the upstream [Key Design Principles](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Roles in DisaggregatedSet

Roles represent specialized functional components of the serving pipeline, such as `prefill` and `decode`. Each role defines its own pod template, replica count per slice, and container arguments, allowing distinct resource sizing and hardware configurations. See the upstream [Roles documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Slices in DisaggregatedSet

A slice is a complete, self-contained instance of the multi-role topology containing all defined roles at the configured ratios. Scaling `slices` adds or removes full end-to-end serving units at the current revision without mutating existing running slices. See the upstream [Slices documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Placement Policy in DisaggregatedSet

Placement policies enforce physical co-location of all roles within a slice on the same accelerator interconnect domain (such as an NVLink domain or network sub-block). This minimizes latency and maximizes throughput for cross-role KV-cache transfers (e.g., over NIXL RDMA). See the upstream [Placement Policy documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Independent Role Autoscaling

DisaggregatedSet supports autoscaling individual roles within a slice by delegating scaling control to external autoscalers (such as KEDA). When configured with `scaling: External` on `slices: 1`, external controllers can adjust prefill or decode capacity independently in response to varying queue and latency demands. See the upstream [Independent Role Autoscaling documentation](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).

## Labels, Annotations and Environment Variables

DisaggregatedSet injects standard metadata including `disaggregatedset.x-k8s.io/name`, `disaggregatedset.x-k8s.io/role`, `disaggregatedset.x-k8s.io/slice`, and `disaggregatedset.x-k8s.io/revision` into child resources and pods. These labels are consumed by the llm-d Router to route requests within slice boundaries and screen revisions during rolling updates. See the upstream [Labels & Annotations reference](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/).
