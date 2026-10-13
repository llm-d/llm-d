# Google Kubernetes Engine (GKE)

This guide covers configuring Google Kubernetes Engine (GKE) clusters and workloads for running high-performance LLM inference with llm-d across NVIDIA GPUs and Google Cloud TPUs.

## Supported Configurations & Prerequisites

llm-d on GKE is tested with the following configurations:

* **GKE Version**: `1.33.4+` (`1.34.0-gke.1626000+` automatically installs GA Gateway API and Gateway API Inference Extension CRDs)
* **NVIDIA GPU Machine Types**: `A3` (H100), `A3 Ultra` (H200), `A4` (B200), `A4X` (GB200)
* **Google Cloud TPU Machine Types**: `ct5p`, `ct5lp` (v5e), `ct6e` (v6e), `tpu7x` (v7 / Ironwood)
* **Gateway**: If using GKE Gateway as your inference gateway, complete the [GKE Inference Gateway environment prerequisites](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#prepare-environment) and see the [GKE Gateway guide](../../gateway/gke.md).

---

## GPU Configuration

* **A3 (H100)**: Deploy a cluster and [configure high-performance networking with GPUDirect-TCPX](https://cloud.google.com/kubernetes-engine/docs/how-to/gpu-bandwidth-gpudirect-tcpx) when running Prefill/Decode (P/D) disaggregation.
* **A3 Ultra (H200), A4 (B200), and A4X (GB200)**: Follow the [steps for creating an AI-optimized GKE cluster with GPUs](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute) and enable GPUDirect RDMA.

### GPU Dynamic Resource Allocation (DRA) and DRANET (RoCE) on GKE

This section provides step-by-step instructions for preparing a GKE cluster for Prefill/Decode (P/D) disaggregation or Wide Expert Parallelism using **Dynamic Resource Allocation (DRA)** for NVIDIA GPUs and GKE-managed RDMA (RoCE) networking (**DRANET**, e.g. NVIDIA H200 GPUs on A3 Ultra).

> [!NOTE]
> Follow the official Google Cloud documentation for the latest updates and detailed instructions:
>
> * [GKE AI Hypercomputer Custom Provisioning](https://docs.cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom)
> * [Set up GPU Dynamic Resource Allocation (DRA)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/set-up-dra)
> * [Allocate network resources by using GKE managed DRANET](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/allocate-network-resources-dra#use-rdma-interfaces-gpu)

Set up the environment variables for cluster provisioning:

```bash
export PROJECT="<your GCP project>"
export LOCATION="<your GCP location>"
export CLUSTER_NAME="<your cluster name>"
export NAMESPACE="llm-d-pd-disaggregation"
```

#### 1. Create the Cluster (Managed DRANET)

GKE network DRA (**DRANET**) supports managed networking out of the box without custom networking drivers, provided **Dataplane V2** is enabled when creating the cluster:

```bash
gcloud container clusters create "${CLUSTER_NAME}" \
    --enable-dataplane-v2 \
    --managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS \
    --location="${LOCATION}" \
    --project="${PROJECT}"
```

> [!NOTE]
> **Deploying on an Existing GKE Cluster:**
>
> * **With Dataplane V2 Enabled:** Skip Step 1 and proceed directly to Step 2 using the automated GKE-managed DRANet profile (`--accelerator-network-profile=auto`).
> * **Without Dataplane V2 Enabled:** GKE-managed automated DRANet is not supported. Instead of running Step 2 as written, manually configure multi-network subnets and map node pool network interfaces to them as described in [Set up multi-network support for pods](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/setup-multinetwork-support-for-pods), then proceed to Step 3.

#### 2. Create the Node Pool (Enable DRANET & Disable Default GPU Driver)

Because GPU DRA is not yet automatically managed by GKE, disable the automated GPU driver and default device plugin via `--accelerator gpu-driver-version=disabled`. Enable GKE-managed **DRANET** with `--accelerator-network-profile=auto` and the required node labels:

```bash
gcloud beta container node-pools create a3u-dra-pool-1 \
  --project="${PROJECT}" \
  --location="${LOCATION}" \
  --cluster="${CLUSTER_NAME}" \
  --accelerator type=nvidia-h200-141gb,count=8,gpu-driver-version=disabled \
  --machine-type=a3-ultragpu-8g \
  --num-nodes=2 \
  --spot \
  --accelerator-network-profile=auto \
  --node-labels="cloud.google.com/gke-networking-dra-driver=true,goog-gke-accelerator-type=nvidia-h200-141gb,nvidia.com/gpu.present=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true,gke-no-default-nvidia-gpu-device-plugin=true"
```

#### 3. Install NVIDIA & GPU DRA Drivers

With automated GPU driver management disabled, install the COS NVIDIA Driver DaemonSet and the official NVIDIA GPU DRA Driver manually:

```bash
# 3.1 Install the preloaded COS NVIDIA driver DaemonSet
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml

# 3.2 Install the NVIDIA GPU DRA Driver via Helm
helm install nvidia-dra-driver-gpu nvidia/nvidia-dra-driver-gpu \
    --version="25.8.0" --create-namespace --namespace=nvidia-dra-driver-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
    --set kubeletPlugin.priorityClassName="" \
    --set 'kubeletPlugin.tolerations[0].key=nvidia.com/gpu' \
    --set 'kubeletPlugin.tolerations[0].operator=Exists' \
    --set 'kubeletPlugin.tolerations[0].effect=NoSchedule'
```

#### 4. Verify DRA Device Readiness

Confirm that the DRA driver pods are running and `ResourceSlices` are populated with both `gpu.nvidia.com` and `mrdma.google.com` devices:

```bash
kubectl get pods -n nvidia-dra-driver-gpu
```

```console
NAME                                         READY   STATUS    RESTARTS   AGE
nvidia-dra-driver-gpu-kubelet-plugin-52cdm   1/1     Running   0          46s
```

```bash
kubectl get resourceslices -o yaml
```

<details>
<summary><b>Click to view expected output</b></summary>

```yaml
apiVersion: v1
items:
- apiVersion: resource.k8s.io/v1
  kind: ResourceSlice
  metadata:
    name: gpu-slice-0
  spec:
    devices:
    - attributes:
        productName:
          string: NVIDIA H200
        resource.kubernetes.io/pcieRoot:
          string: pci0000:00
        type:
          string: gpu
      name: gpu-0
    driver: gpu.nvidia.com
    nodeName: a3u-dra-pool-1-node
- apiVersion: resource.k8s.io/v1
  kind: ResourceSlice
  metadata:
    name: nic-slice-0
  spec:
    devices:
    - attributes:
        resource.kubernetes.io/pcieRoot:
          string: pci0000:00
        type:
          string: nic
      name: nic-0
    driver: mrdma.google.com
    nodeName: a3u-dra-pool-1-node
```

</details>

### GKE A4X (GB200) Setup

For GKE A4X (NVIDIA GB200) clusters, deploy model servers using the `gke/a4x` overlay path (`modelserver/gpu/vllm/gke/a4x`), which inherits from `gke/base` and enables native InfiniBand RDMA networking via DRANet (`mrdma.google.com`) and GPUDirect hardware acceleration:

* **Hardware Interconnect**: Configures `UCX_TLS=rc_mlx5,rc,cuda_copy,cuda_ipc,sm,shm,self` for hardware-accelerated InfiniBand transport.
* **DRANet Resource Claims**: Uses `gke-rdma-nic-template` to request DRANet (`mrdma.google.com`) interfaces while managing GPUs via the NVIDIA Device Plugin (`nvidia.com/gpu`).

### Configuring Support for RDMA on GKE Workload Pods

GKE provides ConnectX-7 (CX-7) RDMA support on A3 Ultra and newer GPU hosts via RoCE. Model servers that use fast internode networking for P/D disaggregation or Wide Expert Parallelism must request RDMA resources on their workload pods; see the [GKE AI Hypercomputer pod manifest configuration guide](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#configure-pod-manifests-rdma).

In addition, expert-parallel deployments using DeepEP with NVIDIA NVSHMEM must either run pods with `privileged: true` to perform GPU-initiated RDMA connections, or enable `PeerMappingOverride=1` in the NVIDIA kernel module parameters via a [manual GPU driver installation](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers).

> [!NOTE]
> While GDRCopy allows CPU-initiated RDMA connections, llm-d has not measured a benefit from this configuration and recommends the default GPU-initiated setting. To silence the GDRCopy warning during NVSHMEM initialization, set `NVSHMEM_DISABLE_GDRCOPY=1` on the model server container.

### Network Topology-Aware Scheduling for Multi-Host GPU Replicas

Multi-host replicas should be co-located on the same high-bandwidth network fabric. On RDMA-enabled GKE nodes, the `cloud.google.com/gce-topology-block` and `cloud.google.com/gce-topology-subblock` labels identify machines within the same fast network domain.

GKE recommends [Topology Aware Scheduling with Kueue and LeaderWorkerSet](https://cloud.google.com/ai-hypercomputer/docs/workloads/schedule-gke-workloads-tas) for multi-host inference workloads (see [Model Server Orchestration (LWS & Kueue)](../../multi-node.md)). For smaller expert-parallel deployments (2- or 4-node replicas) without Kueue, set a pod affinity rule to prefer placing pods within the same `cloud.google.com/gce-topology-block` and `cloud.google.com/gce-topology-subblock`:

```yaml
  affinity:
    podAffinity:
      # Subblock affinity cannot guarantee all pods in the replica
      # are in the same subblock, but is better than random spreading
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 2
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: vllm-deepseek-ep
          matchLabelKeys:
          - component
          topologyKey: cloud.google.com/gce-topology-block
      - weight: 1
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: vllm-deepseek-ep
          matchLabelKeys:
          - component
          topologyKey: cloud.google.com/gce-topology-subblock
```

---

## TPU Configuration

For general TPU node pool setup on GKE (`ct5p`, `ct5lp`, `ct6e`, `tpu7x`), follow the [TPUs in GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/tpus).

### TPU Dynamic Slicing on GKE

This section covers the llm-d-specific configuration for serving model servers on GKE Ironwood (TPU7x) dynamic sub-slices. It is the infrastructure prerequisite for the dynamic-slice recipes in the well-lit path guides:

* [Optimized Baseline on TPU sub-slices](../../../../guides/optimized-baseline/README.md#2-deploy-the-model-server)
* [P/D Disaggregation on TPU sub-slices](../../../../guides/pd-disaggregation/README.md#2-deploy-the-model-server)

#### Dynamic Slicing Overview

[Dynamic slicing](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing) decouples TPU provisioning from slice allocation. Ironwood (TPU7x) capacity is provisioned as fixed `4x4x4` sub-blocks (16 `tpu7x-standard-4t` nodes, 64 chips) with no active Inter-Chip Interconnect, and a GKE-managed slice controller forms slices at scheduling time from `Slice` custom resources. **Dynamic sub-slicing** partitions a sub-block into independent `2x2x1`, `2x2x2`, `2x2x4`, or `2x4x4` slices, allowing aggregated replicas, prefill workers, and decode workers of different shapes to share the same pre-provisioned capacity.

GKE supports two consumption models: a custom scheduler that manages `Slice` resources directly, or [Kueue](https://kueue.sigs.k8s.io/) with Topology-Aware Scheduling (TAS), where a Kueue admission check creates and manages `Slice` resources automatically. The llm-d recipes use the **Kueue TAS** path.

#### Dynamic Slicing Prerequisites

Prepare the cluster by following [Use dynamic slicing in GKE with Kueue](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing):

1. [Requirements](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#requirements): GKE Standard on the Rapid channel, TPU7x, an All Capacity mode reservation, and the minimum Kueue, JobSet, and LeaderWorkerSet (LWS) versions for sub-slicing. LWS is required: the llm-d recipes deploy every model server replica as a `LeaderWorkerSet` group, and the P/D recipe generates its LeaderWorkerSets from a `DisaggregatedSet`, which requires LWS `v0.11.1` or newer (above the minimum on the GKE page).
2. [Enable the slice controller](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#enable_the_slice_controller)
3. [Install Kueue, JobSet, and LWS](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#install-components)
4. [Create node pools with incremental provisioning](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#create-tpu-node-pools): a `provision_only` workload policy, then one 16-node pool per reservation sub-block.
5. [Install the Kueue slice controller](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#install_kueue_slice_controller)
6. [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices#verify-nodes-partitions): every node should carry `cloud.google.com/gke-tpu-partition-<shape>-id` and `-state` labels for each sub-slice shape.

#### Kueue Resources for llm-d

The Kueue resources published in the GCP guide target super-slicing. Sub-slicing requires a `Topology` that enumerates the full partition hierarchy of a sub-block, from `cloud.google.com/gce-topology-block` down through each `cloud.google.com/gke-tpu-partition-<shape>-id` label to `kubernetes.io/hostname`. [`kueue-tas.yaml`](./dynamic-slicing/kueue-tas.yaml) provides that `Topology` together with a `ResourceFlavor`, an `AdmissionCheck` delegating slice formation to the slice controller (`accelerator.gke.io/slice`), and a `ClusterQueue` covering `google.com/tpu`, `cpu`, and `memory`. Apply it once per cluster:

```bash
kubectl apply -f dynamic-slicing/kueue-tas.yaml
```

Then create the [`LocalQueue`](./dynamic-slicing/kueue-localqueue.yaml) in every namespace that runs dynamic-slice model servers, e.g. for the P/D disaggregation guide:

```bash
kubectl apply -n llm-d-pd-disaggregation -f dynamic-slicing/kueue-localqueue.yaml
```

#### Dynamic Slicing Workload Requirements

The llm-d dynamic-slice recipes configure the following fields on every model server pod:

| Field | Value |
| --- | --- |
| Workload label | `kueue.x-k8s.io/queue-name: <LocalQueue name>` on the `LeaderWorkerSet` metadata; for a `DisaggregatedSet`, in each role's `metadata.labels`, which the controller copies to the LeaderWorkerSets it generates |
| LWS `groupIdentity` | `Ordinal` (the default). The Kueue LeaderWorkerSet integration creates one Workload per numeric `group-index` label and ungates pods not owned by a StatefulSet; `Hash` mode satisfies neither, so its groups are never admitted |
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

Slice names are limited to 49 characters and Kueue derives them from the namespace, workload name, and replica index, so keep namespace plus `LeaderWorkerSet` names short. A `DisaggregatedSet` names each generated LeaderWorkerSet `<set>-<slice>-<revision>-<role>` with an 8-character revision, and the Kueue LeaderWorkerSet integration bounds that name to 39 characters, so a set with a `prefill` role needs a name of at most 21 characters (the P/D recipe uses `pd-disagg-tpu-vllm`).

#### Dynamic Slicing Operations

Slice status (`ACTIVATING`, `ACTIVE`, `FAILED`, `INCOMPLETE`) is visible with `kubectl get slices -A`; see [Monitor the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#monitor-dynamic-slicing) for status details and Cloud Monitoring metrics. When tearing down, delete the `LeaderWorkerSet` resources (or the `DisaggregatedSet` that owns them) first so Kueue removes the `Slice` resources it created; active slices block node pool deletion. See [Clean up](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing#clean-up).

---

## Model Storage & Monitoring

### Mitigating Hugging Face Model Download Rate Limiting

When deploying multi-replica prefill and decode pools on GKE, concurrent model weight downloads across pods can trigger Hugging Face HTTP `429` Rate Limiting errors and container startup timeouts.

For production workloads on GKE, store model weights in a Google Cloud Storage (GCS) bucket and mount them directly into pods to eliminate external Hugging Face downloads during scaling:

* [GKE Hugging Face to GCS Transfer Guide](https://gke-ai-labs.dev/docs/tutorials/storage/hf-gcs-transfer/)
* [Load and Cache Model Weights (Operations)](../../../operations/startup/model-loading-and-startup.md)

### Monitoring & Telemetry

We recommend enabling:

* **Google Managed Prometheus** and [automatic application monitoring](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) for automatic metrics collection and dashboards for model servers deployed on the cluster (see also [Monitor GKE TPUs](../../../operations/observability/tpu.md)).
* **[Managed OpenTelemetry](https://cloud.google.com/kubernetes-engine/docs/how-to/managed-otel-gke)** to collect OTLP telemetry such as distributed traces across the llm-d stack. Passing `--managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS` during cluster creation deploys both the OpenTelemetry Collector (in the `gke-managed-otel` namespace) and the `Instrumentation` CRD for workload auto-injection.

---

## Known Issues

### `Undetected platform` on vLLM 0.10.0 on GKE

The GKE-managed GPU driver mounts the node CUDA driver at `/usr/local/nvidia`. CUDA applications like vLLM must include `/usr/local/nvidia/lib64` in `LD_LIBRARY_PATH` to locate the driver libraries, otherwise startup fails with:

```text
INFO 05-28 14:02:21 [__init__.py:247] No platform detected, vLLM is running on UnspecifiedPlatform
...
RuntimeError: Failed to infer device type, please set the environment variable `VLLM_LOGGING_LEVEL=DEBUG` to turn on verbose logging to help debug the issue.
```

The CUDA 12.8 and 12.9 NVIDIA Docker base images changed the installed CUDA driver path from `/usr/local/nvidia` to `/usr/local/cuda` and altered `LD_LIBRARY_PATH`. This is resolved in llm-d container images and upstream vLLM after commit [`5546acb463243ce`](https://github.com/vllm-project/vllm/commit/5546acb463243ce3c166dc620c764a93351b7c69). If you build a custom vLLM image, ensure `LD_LIBRARY_PATH` includes `/usr/local/nvidia/lib64`.

### Google InfiniBand 1.10 Required for vLLM 0.11.0+ (gIB)

vLLM v0.11.0 and newer require NCCL 2.27, which requires Google InfiniBand (gIB) `1.10+`. Follow the GKE AI Hypercomputer guide to [install the RDMA binary and configure NCCL](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#install-rdma-configure-nccl) using at least the RDMA installer DaemonSet version from [container-engine-accelerators#511](https://github.com/GoogleCloudPlatform/container-engine-accelerators/pull/511).

### NVSHMEM Reports `Unable to create ah.` on Initialization for DeepEP

NVSHMEM versions `3.3.20` to `3.4.5` did not zero-initialize the `ibv_ah_attr` struct passed to device initialization, causing Linux kernels that validate `static_rate` to return `EINVAL` (`Unable to create ah.`) when initializing DeepEP kernels for Wide Expert Parallelism. llm-d container images apply a [patch to NVSHMEM](https://github.com/llm-d/llm-d/pull/407) that zero-initializes the struct prior to the kernel call.

### NVSHMEM Fails to Initialize IBGDA Transport for DeepEP

When starting Wide Expert Parallel deployments using DeepEP kernels, container images not compiled against Mellanox OFED drivers may fail with:

```text
/tmp/nvshmem_src/src/modules/transport/ibgda/ibgda.cpp 3888 no active IB device that supports GPU-initiated communication is found, exiting...
/tmp/nvshmem_src/src/host/transport/transport.cpp:nvshmemi_transport_init:282: init failed for transport: IBGDA
```

Default llm-d images based on RHEL UBI are not affected. For custom Ubuntu-based images, add the Mellanox OFED apt repository before installing `libibverbs-dev` or `rdma-core-devel`:

```bash
wget -qO - https://www.mellanox.com/downloads/ofed/RPM-GPG-KEY-Mellanox | apt-key add -
cd /etc/apt/sources.list.d/ && wget https://linux.mellanox.com/public/repo/mlnx_ofed/24.10-0.7.0.0/ubuntu22.04/mellanox_mlnx_ofed.list
```
