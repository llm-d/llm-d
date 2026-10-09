# Disaggregated Serving: Operations (DisaggregatedSet)

The NVIDIA GPU paths of the [P/D disaggregation](../../../guides/pd-disaggregation/README.md) and [wide-ep](../../../guides/wide-ep/README.md) guides run prefill and decode as one LWS [DisaggregatedSet](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) with `groupIdentity: Hash` (`pd-disagg-vllm` and `pd-disagg-sglang` in P/D). The controller runs each role of each slice as a LeaderWorkerSet named `<ds>-<slice>-<revision>-<role>`. The revision changes on every rollout, so query them by label:

```bash
kubectl get leaderworkerset -n ${NAMESPACE} -l disaggregatedset.x-k8s.io/name=pd-disagg-vllm
```

* Scaling `slices` adds or removes complete P/D copies at the current revision, without touching existing slices.
* Role `replicas` apply per slice, so the xPyD ratio (see [When to use P/D and how to tune it](../../architecture/advanced/disaggregation/README.md#when-to-use-pd-and-how-to-tune-it)) holds in every slice.
* Rolling updates proceed independently per slice.
* Changing either role's template rolls both roles; one revision covers all roles.

See the [LWS DisaggregatedSet docs](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) for scaling, rollouts, and placement.

## Pinning Slices to Accelerator Domains (Placement Policy)

Placement policy pins each slice to one topology domain and spreads slices across domains, keeping prefill-to-decode KV-cache transfer inside the low-latency fabric. To enable it, uncomment `placementPolicy` in the set's manifest (for P/D, [`modelserver/gpu/vllm/base/disaggregatedset.yaml`](../../../guides/pd-disaggregation/modelserver/gpu/vllm/base/disaggregatedset.yaml)). The P/D guide ships `slices: 1` (one prefill and one decode); placement policy pays off once you raise `slices` to run several copies:

```yaml
spec:
  slices: 2
  placementPolicy:
    type: ExclusiveSlice
    topology: cloud.google.com/gce-topology-subblock
```

Set `topology` to the node label key that identifies the interconnect domain on your platform:

| Platform | Node label key | Domain it identifies |
| --- | --- | --- |
| GKE GPU (A3 Ultra, A4, A4X) | `cloud.google.com/gce-topology-subblock` | Sub-block (rack). On A4X (GB200) each sub-block is one NVL72 NVLink domain |
| EKS | `topology.k8s.aws/network-node-layer-3` | Finest layer of the instance network topology |
| EKS (GB200 UltraServers) | `topology.k8s.aws/ultraserver-id` | NVL72 NVLink domain |
| CoreWeave | `ds.coreweave.com/nvlink.domain` | NVL72 NVLink domain |
| Any NVIDIA + GPU Feature Discovery | `nvidia.com/gpu.clique` | NVLink domain (`<ClusterUUID>.<CliqueID>`) |

The injected affinity is required, so check the key exists on your accelerator nodes first (`kubectl get nodes -L <key>`). On GKE, the `gce-topology-*` labels appear only on reservation-bound dense capacity (automatic on A3 Ultra, A4, and A4X). For Spot or on-demand nodes, use a coarser key such as `cloud.google.com/gke-nodepool` or `topology.kubernetes.io/zone`.

## Keeping P/D Pairs Inside a Slice (Router Topology Affinity)

The router discovers prefill and decode pods as flat pools and may pair a prefill in one slice with a decode in another. To keep pairs inside a slice, use the router's [topology-affinity plugins](https://github.com/llm-d/llm-d-router/blob/main/pkg/epp/framework/plugins/datalayer/attribute/topology/README.md) with the `disaggregatedset.x-k8s.io/slice` pod label as the tightest level:

```yaml
plugins:
- type: topology-extractor
  parameters:
    hostname: disaggregatedset.x-k8s.io/slice # "same host" now means "same slice"
```

Add `topology-affinity-scorer` (prefer) or `topology-affinity-filter` (require) to the prefill scheduling profile; see the [sample P/D topology EPP config](https://github.com/llm-d/llm-d-router/blob/main/deploy/config/pd-topology-epp-config.yaml). Same slice implies physical proximity only when placement policy is enabled.

## Autoscaling Roles

To autoscale a role, set its scaling mode to `External` and set `slices: 1` (the webhook rejects `slices > 1` while any role uses `External`):

```yaml
roles:
  - name: prefill
    scaling:
      mode: External # default is Static
    spec:
      ...
```

Then point HPA, KEDA, or any `/scale`-aware autoscaler at the auto-created `DisaggregatedSetRoleScaler` named `<ds>-<role>` (for example `pd-disagg-vllm-prefill`). See the [LWS autoscaling example](https://lws.sigs.k8s.io/docs/examples/disaggregatedset/autoscaling/) and the [KEDA token-aware P/D guide](../../../guides/workload-autoscaling/keda-epp-token-aware/README.md).

## Known Issue: Rolling Updates Can Stall on Fully Allocated Clusters

The controller creates new pods before draining their old counterparts. On a cluster with no spare accelerators the new pods stay Pending and the rollout never progresses. Keep at least one decode's worth of free accelerators before a template change (for example by scaling `slices` down by one first), or scale the old revision's LeaderWorkerSet down manually.
