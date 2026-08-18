# TPU Dynamic Slicing on GKE

This document covers configuring a GKE cluster to serve llm-d workloads on dynamically provisioned TPU sub-slices. It is the infrastructure prerequisite for the dynamic-slice model server recipes in the well-lit path guides:

* [Optimized Baseline on TPU sub-slices](../../../../../guides/optimized-baseline/modelserver/tpu/v7/vllm-dynamic-slice/README.md)
* [P/D Disaggregation on TPU sub-slices](../../../../../guides/pd-disaggregation/README.tpu.md#pd-on-dynamic-tpu-sub-slices-tpu7x)

## Overview

Dynamic slicing decouples TPU hardware provisioning from slice allocation. Instead of creating one node pool per workload topology, you provision all Ironwood (TPU7x) capacity as fixed `4x4x4` sub-blocks (16 nodes, 64 chips each) with no active Inter-Chip Interconnect (ICI). At scheduling time, a GKE-managed slice controller forms slices on demand by activating ICI/OCS links, driven by a `Slice` custom resource (`slices.accelerator.gke.io`).

Two configurations exist:

* **Dynamic sub-slicing** partitions a sub-block into smaller independent slices: `2x2x1`, `2x2x2`, `2x2x4`, or `2x4x4`. Sub-slices are electrically and network isolated from each other.
* **Dynamic super-slicing** combines multiple sub-blocks into topologies of `4x4x4` and larger.

This document focuses on **sub-slicing**, which is the configuration relevant to inference: llm-d model servers (single replicas, prefill workers, decode workers) typically need 4 to 64 chips each, and sub-slicing lets a heterogeneous mix of such workloads share pre-provisioned sub-blocks.

Properties relevant to llm-d deployments (see the [GCP concepts page](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing) for details and benchmarks):

* **Recovery**: after a hardware failure, the slice controller re-forms the slice on healthy partitions instead of waiting for node pool repair, reducing MTTR by up to 4.5x.
* **Startup**: forming a slice from provisioned capacity is up to 5x faster than creating a static node pool.
* **Blast radius**: a failure affects one partition; per-partition health is exposed as node labels so schedulers avoid unhealthy hardware.
* **Utilization**: prefill, decode, and aggregated replicas of different shapes are carved from the same sub-blocks.

Two consumption models are supported by GKE: bring-your-own-scheduler (managing `Slice` CRs directly) and [Kueue](https://kueue.sigs.k8s.io/) with Topology-Aware Scheduling (TAS), where a Kueue admission check creates and manages `Slice` CRs automatically. This document uses the **Kueue TAS** path, which requires no custom scheduler.

## Prerequisites

| Requirement | Version / Value |
| --- | --- |
| Cluster type | GKE Standard, Rapid channel |
| GKE version | 1.36.0-gke.3712000+ (sub-slicing) |
| Accelerator | Ironwood (TPU7x), Container-Optimized OS nodes |
| Reservation | All Capacity mode (TPU Cluster Director) |
| Kueue | v0.18.2+ |
| JobSet | v0.12.0+ |
| LeaderWorkerSet (LWS) | v0.8.0+ |

The llm-d TPU recipes deploy multi-host model servers as `LeaderWorkerSet` resources, so LWS is required.

## Cluster Configuration

Follow the GCP documentation for the authoritative procedure; the steps below summarize it with the values used by the llm-d recipes.

* [Create dynamic slices](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices)
* [Use dynamic slicing with Kueue](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing)

### 1. Enable the slice controller

```bash
gcloud container clusters update ${CLUSTER_NAME} \
    --location=${LOCATION} \
    --enable-slice-controller
```

Verify the CRD is installed:

```bash
kubectl get crd slices.accelerator.gke.io
```

### 2. Create a workload policy for incremental provisioning

Nodes are provisioned without forming ICI/OCS links (`provision_only`); links are activated later per `Slice` CR. The topology is always `4x4x4` (one sub-block) regardless of the sub-slice shapes you will run:

```bash
gcloud compute resource-policies create workload-policy ${WORKLOAD_POLICY_NAME} \
    --project=${PROJECT_ID} \
    --region=${REGION} \
    --type=HIGH_THROUGHPUT \
    --accelerator-topology=4x4x4 \
    --accelerator-topology-mode=provision_only
```

### 3. Create node pools

Create one node pool per reservation sub-block (16 `tpu7x-standard-4t` nodes = 64 chips):

```bash
gcloud container node-pools create ${NODE_POOL_NAME} \
    --cluster=${CLUSTER_NAME} \
    --node-locations=${ZONE} \
    --machine-type=tpu7x-standard-4t \
    --num-nodes=16 \
    --placement-policy=${WORKLOAD_POLICY_NAME} \
    --reservation-affinity=specific \
    --reservation="projects/${PROJECT_ID}/reservations/${RESERVATION_NAME}/reservationBlocks/${BLOCK_NAME}/reservationSubBlocks/${SUBBLOCK_NAME}"
```

### 4. Verify partition labels

Each node carries partition identity and health labels for every sub-slice shape it belongs to:

```bash
kubectl describe node ${NODE_NAME} | grep -E "cloud.google.com/gke-tpu-partition-.*-(id|state)"
```

Expected label keys follow the pattern `cloud.google.com/gke-tpu-partition-<shape>-id` and `cloud.google.com/gke-tpu-partition-<shape>-state` for shapes `2x2x1`, `2x2x2`, `2x2x4`, `2x4x4`, and `4x4x4`. State values are `HEALTHY`, `UNHEALTHY`, `UNSET`, `INCOMPLETE`, and (for `4x4x4` only) `DEGRADED`.

## Kueue TAS Configuration

### 1. Install Kueue, JobSet, and LWS

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml
kubectl apply --server-side -f https://github.com/kubernetes-sigs/jobset/releases/download/${JOBSET_VERSION}/manifests.yaml
kubectl apply --server-side -f https://github.com/kubernetes-sigs/lws/releases/download/${LWS_VERSION}/manifests.yaml
```

The Kueue controller configuration must enable the LeaderWorkerSet integration (`leaderworkerset.x-k8s.io/leaderworkerset` in `integrations.frameworks`) for LWS-based model servers to be admitted.

### 2. Install the Kueue slice controller

The Kueue slice controller translates admitted workloads into `Slice` CRs. It runs in the `slice-controller-system` namespace and registers a mutating webhook for `Job`, `JobSet`, and `LeaderWorkerSet` creation. Install it from the manifest published in the [GCP how-to guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#install-kueue-slice-controller).

If a workload does not select a partition health state, the webhook injects a node affinity requiring `HEALTHY` or `DEGRADED` partitions. The llm-d recipes select `HEALTHY` partitions explicitly.

### 3. Configure Kueue resources

Apply the cluster-scoped Kueue resources - a `Topology` describing the TPU7x partition hierarchy, a `ResourceFlavor`, an `AdmissionCheck` delegating slice formation to the slice controller, and a `ClusterQueue`:

```bash
kubectl apply -f kueue-tas.yaml
```

See [`kueue-tas.yaml`](./kueue-tas.yaml).

<!-- TODO(reviewer): the published GCP sample Topology covers super-slicing only
     (gce-topology-block / partition-4x4x4-id / hostname). The sub-slice Topology
     levels in kueue-tas.yaml are extrapolated from the partition label hierarchy
     and need verification against the GKE reference configuration. -->

Then create a `LocalQueue` in every namespace that runs llm-d model servers, e.g. for the P/D disaggregation guide:

```bash
kubectl apply -n llm-d-pd-disaggregation -f kueue-localqueue.yaml
```

See [`kueue-localqueue.yaml`](./kueue-localqueue.yaml).

## Workload Requirements

A model server pod scheduled onto a dynamic sub-slice needs the following. The llm-d dynamic-slice recipes set all of these; they are listed here for users adapting their own manifests:

| Field | Value |
| --- | --- |
| Workload label | `kueue.x-k8s.io/queue-name: <LocalQueue name>` |
| Pod annotation | `cloud.google.com/gke-tpu-slice-topology: "<shape>"` (e.g. `2x2x2`) |
| Pod nodeSelector | `cloud.google.com/gke-tpu-accelerator: tpu7x` |
| Pod nodeSelector (health) | `cloud.google.com/gke-tpu-partition-<shape>-state: "HEALTHY"` |
| Toleration | key `google.com/tpu`, effect `NoSchedule` |
| Resources | `google.com/tpu: 4` per pod (requests and limits) |

Do **not** set the static `cloud.google.com/gke-tpu-topology` nodeSelector used by conventional TPU node pools; slice placement is resolved by Kueue and the slice controller.

Sizing rule: each `tpu7x-standard-4t` node has 4 chips and each TPU7x chip has 2 cores, so a shape `AxBxC` maps to `(A*B*C)/4` pods per slice and supports `--tensor-parallel-size` up to `A*B*C*2`:

| Shape | Chips | Pods per slice (LWS `size`) | Cores (max TP) |
| --- | --- | --- | --- |
| `2x2x1` | 4 | 1 | 8 |
| `2x2x2` | 8 | 2 | 16 |
| `2x2x4` | 16 | 4 | 32 |
| `2x4x4` | 32 | 8 | 64 |

## Verification and Monitoring

After deploying a workload, confirm that slices form and activate:

```bash
kubectl get slices -A
kubectl describe slice ${SLICE_NAME}
```

Slice status progresses through `ACTIVATING` to `ACTIVE`; failure states include `FAILED` and `INCOMPLETE`. Cloud Monitoring exposes `kubernetes.io/accelerator/slice/state`, `.../partition/state`, and slice formation/deformation duration metrics.

## Cleanup

Delete workloads (the LWS or JobSet) first; Kueue then deletes the `Slice` CRs it created. Active `Slice` CRs block node pool deletion. Avoid manually modifying `Slice` CRs created by Kueue.

## Known Constraints

* Sub-slicing requires TPU7x; earlier TPU generations (v6e and older) use static node pool topologies.
* Slice names are limited to 49 characters; Kueue derives them from namespace, workload name, and replica index, so keep namespace plus LWS names short.
* Node pool upgrades with active sub-slices move those slices to `FAILED`; use `--max-surge-upgrade=0 --max-unavailable-upgrade=16` to upgrade a full sub-block at once.
