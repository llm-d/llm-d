# llm-d on Google Kubernetes Engine (GKE)

This document covers configuring GKE clusters for running high performance LLM inference with llm-d.

## Prerequisites

llm-d on GKE is tested with the following configurations:

  * Machine types: A3, A4, ct5p, ct5lp, ct6e
  * Versions: GKE 1.32.3+

## Cluster Configuration

The GCP cluster should be configured with the following settings:

* All prerequisites for [GKE Inference Gateway enablement](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#prepare-environment)

For A3 machines, deploy a cluster and [configure high performance networking with TCPX](https://cloud.google.com/kubernetes-engine/docs/how-to/gpu-bandwidth-gpudirect-tcpx) if you plan to leverage Prefill/Decode disaggregation.

For A3 Ultra, A4, and A4X machines, follow the [steps for creating an AI-optimized GKE cluster with GPUs](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute) and enable GPUDirect RDMA.

For all TPU machines, follow the [TPUs in GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/tpus).

We recommend enabling Google Managed Prometheus and [automatic application monitoring](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) to enable automatic metrics collection and dashboards for vLLM deployed on the cluster.

## Workload Configuration

### GPUs

#### Configuring support for RDMA on GKE workload pods

GCP provides CX-7 support on A3 Ultra+ GPU hosts via RoCE.

Model servers that need to use fast internode networking for P/D disaggregation or wide expert parallelism will need to request RDMA resources on your workload pods. The cluster creation guide describes the required changes to a pod to access the RDMA devices (e.g. [for A3 Ultra / A4](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#configure-pod-manifests-rdma)).

#### Ensuring network topology aware scheduling of pod replicas with RDMA

Select appropriate node selectors to ensure multi-host replicas are all colocated on the same network fabric. For many deployments, your A3 Ultra or newer reservation will be within a single zone. This ensures reachability but may not achieve the desired aggregate throughput.

On RDMA enabled instances the `cloud.google.com/gce-topology-block` label identifies machines within the same fast network and can be used as the primitive for higher level orchestration to group the pods within a multi-host replica.

GKE recommends using [Topology Aware Scheduling with Kueue and LeaderWorkerSet](https://cloud.google.com/ai-hypercomputer/docs/workloads/schedule-gke-workloads-tas) for multi-host training and inference workloads.

## Known Issues

### `Undetected platform` on vLLM 0.10.0 on GKE

The 12.8 and 12.9 NVIDIA CUDA Docker images used as a base for vLLM moved the location of the installed CUDA drivers from `/usr/local/nvidia` to `/usr/local/cuda`, which prevents GKE managed GPU drivers from being detected.

Until [vLLM issue #18859](https://github.com/vllm-project/vllm/issues/18859) is resolved by updating vLLM to the CUDA 13 base image, users will need to ensure their LD_LIBRARY_PATH in their vLLM image includes `/usr/local/nvidia/lib64`.

#### Google InfiniBand 1.10 required for vLLM 0.11.0 (gIB)

vLLM v0.11.0 and newer require NCCL 2.27, which is supported in gIB 1.10+. See the appropriate section in cluster configuration for installing the RDMA binary and configuring NCCL (e.g. [for A3 Ultra / A4](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#install-rdma-configure-nccl)).  To get 1.10, use at least the version of the RDMA installer DaemonSet described in this [1.10 pull request](https://github.com/GoogleCloudPlatform/container-engine-accelerators/pull/511).
