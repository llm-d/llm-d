# TPU Dynamic Slicing on GKE

This document covers the llm-d-specific configuration for serving model servers on GKE TPU7x dynamic sub-slices. Cluster preparation is documented by Google Cloud and linked below rather than repeated here. It is the infrastructure prerequisite for the dynamic-slice recipes in the well-lit path guides:

* [Optimized Baseline on TPU sub-slices](../../../../../guides/optimized-baseline/modelserver/tpu/v7/vllm-dynamic-slice/README.md)
* [P/D Disaggregation on TPU sub-slices](../../../../../guides/pd-disaggregation/README.tpu.md#pd-on-dynamic-tpu-sub-slices-tpu7x)

## Overview

[Dynamic slicing](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing) decouples TPU provisioning from slice allocation. Ironwood (TPU7x) capacity is provisioned as fixed `4x4x4` sub-blocks (16 `tpu7x-standard-4t` nodes, 64 chips) with no active Inter-Chip Interconnect, and a GKE-managed slice controller forms slices at scheduling time from `Slice` custom resources. **Dynamic sub-slicing** partitions a sub-block into independent `2x2x1`, `2x2x2`, `2x2x4`, or `2x4x4` slices, which lets aggregated replicas, prefill workers, and decode workers of different shapes share the same pre-provisioned capacity.

GKE supports two consumption models: a custom scheduler that manages `Slice` resources directly, or [Kueue](https://kueue.sigs.k8s.io/) with Topology-Aware Scheduling (TAS), where a Kueue admission check creates and manages `Slice` resources automatically. The llm-d recipes use the **Kueue TAS** path.

## Prerequisites

Prepare the cluster by following [Use dynamic slicing in GKE with Kueue](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing):

1. [Requirements](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#requirements): GKE Standard on the Rapid channel, TPU7x, an All Capacity mode reservation, and the minimum Kueue, JobSet, and LeaderWorkerSet (LWS) versions for sub-slicing. LWS is required: the llm-d recipes deploy every model server replica as a `LeaderWorkerSet` group.
2. [Enable the slice controller](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#enable_the_slice_controller)
3. [Install Kueue, JobSet, and LWS](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#install-components)
4. [Create node pools with incremental provisioning](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#create-tpu-node-pools): a `provision_only` workload policy, then one 16-node pool per reservation sub-block.
5. [Install the Kueue slice controller](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#install_kueue_slice_controller)
6. [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices#verify-nodes-partitions): every node should carry `cloud.google.com/gke-tpu-partition-<shape>-id` and `-state` labels for each sub-slice shape.

## Kueue Resources for llm-d

The Kueue resources published in the GCP guide target super-slicing. Sub-slicing needs a `Topology` that enumerates the full partition hierarchy of a sub-block, from `cloud.google.com/gce-topology-block` down through each `cloud.google.com/gke-tpu-partition-<shape>-id` label to `kubernetes.io/hostname`. [`kueue-tas.yaml`](./kueue-tas.yaml) provides that `Topology` together with a `ResourceFlavor`, an `AdmissionCheck` delegating slice formation to the slice controller (`accelerator.gke.io/slice`), and a `ClusterQueue` covering `google.com/tpu`, `cpu`, and `memory`. Apply it once per cluster:

```bash
kubectl apply -f kueue-tas.yaml
```

Then create the [`LocalQueue`](./kueue-localqueue.yaml) in every namespace that runs dynamic-slice model servers, e.g. for the P/D disaggregation guide:

```bash
kubectl apply -n llm-d-pd-disaggregation -f kueue-localqueue.yaml
```

## Workload Requirements

The llm-d dynamic-slice recipes set the following on every model server pod; they are listed for users adapting their own manifests:

| Field | Value |
| --- | --- |
| Workload label | `kueue.x-k8s.io/queue-name: <LocalQueue name>` |
| Pod annotation | `cloud.google.com/gke-tpu-slice-topology: "<shape>"` (e.g. `2x2x2`) |
| Pod nodeSelector | `cloud.google.com/gke-tpu-accelerator: tpu7x` |
| Pod nodeSelector (health) | `cloud.google.com/gke-tpu-partition-<shape>-state: "HEALTHY"` (see [partition health selection](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#define_the_partition_health_selection)) |
| Toleration | key `google.com/tpu`, effect `NoSchedule` |
| Resources | `google.com/tpu: 4` per pod (requests and limits) |

Do **not** set the static `cloud.google.com/gke-tpu-topology` nodeSelector used by conventional TPU node pools; slice placement is resolved by Kueue and the slice controller.

Each `tpu7x-standard-4t` node has 4 chips and each TPU7x chip has 2 cores, so a shape `AxBxC` maps to `(A*B*C)/4` pods per slice and supports `--tensor-parallel-size` up to `A*B*C*2`:

| Shape | Chips | Pods per slice (LWS `size`) | Cores (max TP) |
| --- | --- | --- | --- |
| `2x2x1` | 4 | 1 | 8 |
| `2x2x2` | 8 | 2 | 16 |
| `2x2x4` | 16 | 4 | 32 |
| `2x4x4` | 32 | 8 | 64 |

Slice names are limited to 49 characters and Kueue derives them from the namespace, workload name, and replica index, so keep namespace plus `LeaderWorkerSet` names short.

## Operations

Slice status (`ACTIVATING`, `ACTIVE`, `FAILED`, `INCOMPLETE`) is visible with `kubectl get slices -A`; see [Monitor the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#monitor-dynamic-slicing) for status details and Cloud Monitoring metrics. When tearing down, delete the `LeaderWorkerSet` resources first so Kueue removes the `Slice` resources it created; active slices block node pool deletion. See [Clean up](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#clean-up).
