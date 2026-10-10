# LeaderWorkerSet (LWS)

[LeaderWorkerSet (LWS)](https://lws.sigs.k8s.io/docs/concepts/) is a Kubernetes SIG Apps API designed for multi-pod distributed workloads where each replica consists of a group of tightly coupled pods: a leader pod and one or more worker pods.

In `llm-d`, LeaderWorkerSet provides the foundation for multi-node model serving (such as data-parallel and expert-parallel architectures like [Wide Expert Parallelism](../disaggregation/wide-expert-parallelism.md)) and multi-host tensor-parallel setups. It manages pod group lifecycle, provides all-or-nothing restart semantics, injects network rank identity, and coordinates exclusive placement across accelerator domains.

> [!NOTE]
> This page is an in-repo reference overview for `llm-d`. Full documentation is mirrored on `llm-d.ai` from the upstream [LWS Concepts Documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Architecture and Relationship with StatefulSet

LeaderWorkerSet manages groups of pods rather than individual pods. While Kubernetes StatefulSet provides stable network identities for individual pods, LeaderWorkerSet extends this to hierarchical groups where a leader pod and its worker pods share an identity lifecycle and scale together as a unit. See the upstream [Architecture documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Dual Pod Templates

LeaderWorkerSet supports defining separate specifications for the leader pod and worker pods via dual pod templates. In distributed model serving, the leader pod can run coordinating processes (such as the main API server or rank 0 coordinator) while worker pods run specialized compute worker containers. See the upstream [Dual Pod Templates documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Startup Policy

Startup policies govern the order in which leader and worker pods are initialized. LeaderWorkerSet supports starting the leader first followed by workers (`LeaderCreatedFirst`), or starting all pods concurrently, allowing model servers to synchronize initialization cleanly. See the upstream [Startup Policy documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Exclusive Topology Placement

Exclusive topology placement ensures that all pods in a group are co-located within a designated topology domain (such as an ultra-high-speed NVLink domain, rack, or network switch) without sharing that domain with other groups. In `llm-d`, this guarantees low latency for inter-GPU collectives. See the upstream [Exclusive Placement documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Gang Scheduling

Gang scheduling guarantees all-or-nothing scheduling for all pods within a group, preventing deadlocks where some worker pods are scheduled while others wait indefinitely for resources. LeaderWorkerSet integrates with Kueue and custom schedulers to enforce gang scheduling. See the upstream [Gang Scheduling documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Subgroups

Subgroups allow partitioning large groups of workers into smaller topological fault or network domains within a single LeaderWorkerSet replica. This enables hierarchical collective communications and targeted fault isolation across multi-node clusters. See the upstream [Subgroups documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Volume Claim Templates Support

LeaderWorkerSet supports stateful persistent storage by generating PersistentVolumeClaims for individual pods using volume claim templates. This allows distributed model instances to mount dedicated cache or weight scratch volumes with persistent identity. See the upstream [Volume Claim Templates documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Rollout Strategy

Rollout strategies define how updates to the pod template propagate across group replicas. LeaderWorkerSet supports rolling updates with configurable surge and unavailability thresholds, updating one replica group at a time to maintain serving availability. See the upstream [Rollout Strategy documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Resizing Groups

Resizing allows modifying the size of worker groups or the number of group replicas dynamically. LeaderWorkerSet manages adding or removing worker pods while preserving group identity and operational integrity. See the upstream [Resizing documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Failure Handling and Restart Policies

LeaderWorkerSet provides all-or-nothing failure handling policies. When an unrecoverable failure occurs on any worker pod or accelerator, the entire group is restarted together to prevent hung distributed collectives and ensure clean state recovery. See the upstream [Restart Policy documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Group Identity

Each pod within a LeaderWorkerSet receives a deterministic group identity, including the group index and worker index, exposed via environment variables and Kubernetes labels. In `llm-d`, distributed runtimes use these identities to determine distributed rank and host mappings. See the upstream [Group Identity documentation](https://lws.sigs.k8s.io/docs/concepts/).

## Labels, Annotations and Environment Variables

LeaderWorkerSet injects standard Kubernetes labels, annotations, and environment variables into every pod (e.g., `leaderworkerset.sigs.k8s.io/name`, `leaderworkerset.sigs.k8s.io/group-index`, `LWS_LEADER_ADDRESS`). These metadata keys allow services, the llm-d Router, and monitoring tools to discover and track group members. See the upstream [Labels & Annotations reference](https://lws.sigs.k8s.io/docs/concepts/).
