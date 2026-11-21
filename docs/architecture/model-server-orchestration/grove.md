# Grove

[Grove](https://github.com/ai-dynamo/grove) is an open-source Kubernetes API for AI inference workloads that span several roles and several nodes. A single `PodCliqueSet` describes a whole serving system, such as prefill and decode, with its startup order, gang-scheduling requirements, topology placement and scaling units.

In `llm-d`, Grove is an alternative to the [`DisaggregatedSet`](disaggregatedset.md) for the [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md) path on NVIDIA GPU. The [wide-ep guide's `grove/*` overlays](../../../guides/wide-ep/README.md#grove-orchestration) run the same prefill and decode pods as the `DisaggregatedSet` overlays, orchestrated by one `PodCliqueSet`.

> [!NOTE]
> This page is an in-repo reference overview for `llm-d`. See the upstream [Grove user guide](https://github.com/ai-dynamo/grove/tree/main/docs/user-guide) for the full documentation.

## Resource Hierarchy

Grove composes three resources:

* **`PodCliqueSet`**: the whole serving system. Each replica is a complete copy of every role, with its own headless Service for pod discovery.
* **`PodClique`**: a group of pods that share a pod template and a role, such as all the pods of one prefill DP/EP group.
* **`PodCliqueScalingGroup`**: a set of `PodClique`s that scale together. Each scaling group replica adds one copy of every member clique, the way a `LeaderWorkerSet` replica adds one leader-worker group.

The wide-ep overlays wrap each role's two-pod `PodClique` in its own `PodCliqueScalingGroup`, so one scaling group replica is one multi-node DP/EP group. See the upstream [core concepts](https://github.com/ai-dynamo/grove/tree/main/docs/user-guide/01_core-concepts).

## Gang Scheduling and Termination

`minAvailable` on a `PodClique` or `PodCliqueScalingGroup` sets how many pods or replicas must be scheduled together. Grove publishes this intent as `PodGang` resources, which a gang-scheduling backend such as the [KAI Scheduler](https://github.com/NVIDIA/KAI-Scheduler) places all-or-nothing. With Grove's built-in `default-scheduler` backend the pods go to the default Kubernetes scheduler, which does not enforce the gang.

The same `minAvailable` drives failure handling. When a clique stays below `minAvailable` with failing containers for longer than the `PodCliqueSet`'s `terminationDelay`, Grove recreates the failed scaling group replica as a unit, or the whole `PodCliqueSet` replica when losing it would leave the scaling group below its own `minAvailable`. Either way the remaining ranks do not wait on a collective that can never complete. This plays the role of LeaderWorkerSet's all-or-nothing group restart.

## Pod Discovery

Grove gives every pod a deterministic hostname, `<podclique-name>-<pod-index>`, and injects environment variables that describe its place in the hierarchy, such as `GROVE_PCLQ_NAME`, `GROVE_HEADLESS_SERVICE`, `GROVE_PCSG_POD_INDEX` and `GROVE_PCSG_TEMPLATE_NUM_PODS`. Model servers build their distributed ranks from them, as they do from `LWS_LEADER_ADDRESS` and `LWS_WORKER_INDEX` under LeaderWorkerSet. See the upstream [environment variable reference](https://github.com/ai-dynamo/grove/blob/main/docs/user-guide/03_environment-variables-for-pod-discovery/02_env_var_reference.md).

## Topology-Aware Placement

A `topologyConstraint` on a `PodCliqueSet`, `PodCliqueScalingGroup` or `PodClique` packs its pods into one topology domain, such as a rack or an NVLink domain, using the topology levels an administrator declares in a `ClusterTopologyBinding`. See the upstream [topology-aware scheduling guide](https://github.com/ai-dynamo/grove/blob/main/docs/user-guide/topology-aware-scheduling.md). On NVIDIA Multi-Node NVLink systems, Grove can also create the `ComputeDomain` and resource claims for the pods; see [Auto-MNNVL](https://github.com/ai-dynamo/grove/blob/main/docs/user-guide/auto-mnnvl.md).

## Update Strategy

`RollingRecreate`, the default, deletes and recreates one clique pod or scaling group replica at a time, so an update needs no spare capacity. Roles update independently.

Grove has no revision label shared by every role of a version, the way a `DisaggregatedSet` does, so the wide-ep overlays add their own: both cliques carry `llm-d.ai/revision`, bumped by hand for incompatible changes, and the llm-d Router's `disaggregatedset-rollout-screener` is configured to read it (`revisionLabelKey`, `roleLabelKey`) and keep each request's prefill and decode on one revision. See [Grove orchestration](../../../guides/wide-ep/README.md#grove-orchestration).

The `Coherent` strategy instead brings up updated replicas in units that contain every role, scheduled together as a new `PodGang`. See the upstream [coherent rolling updates proposal](https://github.com/ai-dynamo/grove/tree/main/docs/proposals/393-coherent-rolling-updates).

## Installation

See [Install Grove](../../infrastructure/model-server-orchestration.md#alternative-install-grove).
