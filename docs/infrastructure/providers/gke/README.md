# llm-d on Google Kubernetes Engine (GKE)

This document covers configuring GKE clusters for running high performance LLM inference with llm-d.

## Prerequisites

llm-d on GKE is tested with the following configurations:

* Machine types: A3, A4, ct5p, ct5lp, ct6e
* Versions: GKE 1.33.4+

## Cluster Configuration

The GCP cluster should be configured with the following settings:

* All prerequisites for [GKE Inference Gateway enablement](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#prepare-environment)

### GPUs

For A3 machines, deploy a cluster and [configure high performance networking with TCPX](https://cloud.google.com/kubernetes-engine/docs/how-to/gpu-bandwidth-gpudirect-tcpx) if you plan to leverage Prefill/Decode disaggregation.

For A3 Ultra, A4, and A4X machines, follow the [steps for creating an AI-optimized GKE cluster with GPUs](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute) and enable GPUDirect RDMA.

### GPU Dynamic Resource Allocation (DRA) and DRANET (RoCE) on GKE

This section provides specific instructions for deploying P/D (Prefill/Decode) disaggregation on GKE using **Dynamic Resource Allocation (DRA)** for NVIDIA GPUs and RDMA (RoCE) networking (e.g. NVIDIA H200 GPUs on A3 Ultra).

> [!NOTE]
> Follow the official GCP documentation for the latest updates and detailed instructions:
> * [GKE AI Hypercomputer Custom Provisioning](https://docs.cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom)
> * [Set up GPU Dynamic Resource Allocation (DRA)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/set-up-dra)
> * [Allocate network resources by using GKE managed DRANET](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/allocate-network-resources-dra#use-rdma-interfaces-gpu)

#### Prerequisites

Set up the environment variables:

```bash
export PROJECT="<your GCP project>"
export LOCATION="<your GCP location>"
export CLUSTER_NAME="<your cluster name>"
export NAMESPACE="llm-d-pd-disaggregation"
```

#### 1. Create Cluster (Managed DRANET)

GKE network DRA (**DRANET**) supports managed networking out of the box, so you do not need to install custom networking drivers. However, you **must** enable **Dataplane V2** when creating your cluster:

```bash
gcloud container clusters create "${CLUSTER_NAME}" \
    --enable-dataplane-v2 \
    --location="${LOCATION}" \
    --project="${PROJECT}"
```

> [!NOTE]
> **Deploying on an Existing GKE Cluster:**
> If you are deploying on an existing cluster instead of creating a new one, your configuration path depends on whether Dataplane V2 is enabled:
> 
> * **Existing Cluster with Dataplane V2 Enabled:** You can skip Step 1 (creating the cluster) and proceed directly to Step 2 (creating the node pool) using the automated GKE-managed DRANet profile (`--accelerator-network-profile=auto`).
> * **Existing Cluster without Dataplane V2 Enabled:** GKE-managed automated DRANet is not supported. You **CANNOT** directly use the node pool creation command in ***Step 2*** below. Instead, you must **manually** configure and manage the multi-networking for the additional network interfaces (NICs) by manually creating subnetworks and mapping node pool network interfaces to them, as detailed in GKE's [Set up multi-network support for pods](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/setup-multinetwork-support-for-pods) documentation. Once the subnets and node pool are manually provisioned, proceed directly to **Step 3**.


#### 2. Create Node Pool (Enable DRANET & Disable Default GPU Driver)

GPU DRA is not yet fully managed by GKE. Therefore, you must disable the automated GPU driver and the default GPU device plugin via `--accelerator gpu-driver-version=disabled`. 

GKE managed **DRANET** is enabled by configuring `--accelerator-network-profile=auto` and adding the `cloud.google.com/gke-networking-dra-driver=true` node label:

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

Since automated GPU driver management is disabled, you must install the NVIDIA Driver and the NVIDIA GPU DRA Driver manually.

**3.1 Install NVIDIA Driver**

Apply the preloaded COS NVIDIA driver DaemonSet:

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

**3.2 Install NVIDIA GPU DRA Driver**

Install the official NVIDIA DRA Driver:

```bash
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

**3.3 Verify DRA Device Readiness**

Verify that the DRA driver pods are running and `ResourceSlices` are successfully populated with `gpu.nvidia.com` and `mrdma.google.com` devices:

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

### TPUs

For all TPU machines, follow the [TPUs in GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/tpus).

### Monitoring

We recommend enabling Google Managed Prometheus and [automatic application monitoring](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) to enable automatic metrics collection and dashboards for vLLM deployed on the cluster.

## Workload Configuration

### GPUs

#### Configuring support for RDMA on GKE workload pods

GCP provides CX-7 support on A3 Ultra+ GPU hosts via RoCE.

Model servers that need to use fast internode networking for P/D disaggregation or wide expert parallelism will need to request RDMA resources on your workload pods. The cluster creation guide describes the required changes to a pod to access the RDMA devices (e.g. [for A3 Ultra / A4](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#configure-pod-manifests-rdma)).

In addition, expert parallel deployments leveraging DeepEP with NVIDIA NVSHMEM will need to run their pods with `privileged: true` in order to perform GPU-initiated RDMA connections, or enable `PeerMappingOverride=1` in your NVIDIA kernel settings with a [manual GPU driver installation](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers).

**_NOTE:_** While GDRCopy allows CPU-initiated RDMA connections, at the current time we have not measured a benefit to this configuration and instead recommend the default GPU-initiated setting. You can disable the GDRCopy warning in NVSHMEM initialization by setting the `NVSHMEM_DISABLE_GDRCOPY=1` environment variable on your container.

#### Ensuring network topology aware scheduling of pod replicas with RDMA

Select appropriate node selectors to ensure multi-host replicas are all colocated on the same network fabric. For many deployments, your A3 Ultra or newer reservation will be within a single zone. This ensures reachability but may not achieve the desired aggregate throughput.

On RDMA-enabled GKE nodes the `cloud.google.com/gce-topology-block` label identifies machines within the same fast network and can be used as the primitive for higher level orchestration to group the pods within a multi-host replica into the same RDMA network.

GKE recommends using [Topology Aware Scheduling with Kueue and LeaderWorkerSet](https://cloud.google.com/ai-hypercomputer/docs/workloads/schedule-gke-workloads-tas) for multi-host training and inference workloads.

For smaller scale expert parallel deployments (2 or 4 node replicas) we have not observed significant wins in requiring all nodes in the replica to be within the same `cloud.google.com/gce-topology-subblock`. We do recommend setting a pod affinity rule to place all pods within the same `cloud.google.com/gce-topology-block`:

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

## Known Issues

### `Undetected platform` on vLLM 0.10.0 on GKE

The GKE managed GPU driver automatically mounts the configured node CUDA driver at `/usr/local/nvidia`. CUDA applications like vLLM must have `/usr/local/nvidia` in their `LD_LIBRARY_PATH` or they will not be able to locate the necessary CUDA libraries.

In vLLM, this causes startup to fail with the following logging:

```text
INFO 05-28 14:02:21 [__init__.py:247] No platform detected, vLLM is running on UnspecifiedPlatform
...
INFO 05-28 14:02:26 [config.py:1909] Disabled the custom all-reduce kernel because it is not supported on current platform.
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/api_server.py", line 1372, in <module>
    parser = make_arg_parser(parser)
  File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/cli_args.py", line 246, in make_arg_parser
    parser = AsyncEngineArgs.add_cli_args(parser)
  File "/usr/local/lib/python3.12/dist-packages/vllm/engine/arg_utils.py", line 1565, in add_cli_args
    parser = EngineArgs.add_cli_args(parser)
  File "/usr/local/lib/python3.12/dist-packages/vllm/engine/arg_utils.py", line 825, in add_cli_args
    vllm_kwargs = get_kwargs(VllmConfig)
  File "/usr/local/lib/python3.12/dist-packages/vllm/engine/arg_utils.py", line 174, in get_kwargs
    default = field.default_factory()
  File "<string>", line 4, in __init__
  File "/usr/local/lib/python3.12/dist-packages/vllm/config.py", line 2245, in __post_init__
    raise RuntimeError(
RuntimeError: Failed to infer device type, please set the environment variable `VLLM_LOGGING_LEVEL=DEBUG` to turn on verbose logging to help debug the issue.
```

The root cause of this issue is that the CUDA 12.8 and 12.9 NVIDIA Docker images that are used as a base for vLLM container images changed the location of the installed CUDA drivers from `/usr/local/nvidia` to `/usr/local/cuda` and altered `LD_LIBRARY_PATH`. As a result, the libraries from the driver mount are not found by vLLM.

A workaround exists in llm-d container images and vLLM after commit [5546acb463243ce](https://github.com/vllm-project/vllm/commit/5546acb463243ce3c166dc620c764a93351b7c69). Users who customize their vLLM image will need to ensure their LD_LIBRARY_PATH in their vLLM image includes `/usr/local/nvidia/lib64`.

#### Google InfiniBand 1.10 required for vLLM 0.11.0 (gIB)

vLLM v0.11.0 and newer require NCCL 2.27, which is supported in gIB 1.10+. See the appropriate section in cluster configuration for installing the RDMA binary and configuring NCCL (e.g. [for A3 Ultra / A4](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#install-rdma-configure-nccl)).  To get 1.10, use at least the version of the RDMA installer DaemonSet described in this [1.10 pull request](https://github.com/GoogleCloudPlatform/container-engine-accelerators/pull/511).

#### NVSHMEM reports `Unable to create ah.` on initialization for DeepEP

Some versions (3.3.20 to 3.4.5) of NVSHMEM contain a bug where the `ibv_ah_attr` struct passed to device initialization was not zeroed out in code. Versions of the Linux kernel that validate the value of the `static_rate` field could fail to start due to an `EINVAL` and reporting `Unable to create ah.` when using the DeepEP kernels for wide expert parallelism.

To work around this issue llm-d applies a [patch to NVSHMEM](https://github.com/llm-d/llm-d/pull/407) that invokes `memset(..., 0, ...)` on the struct before it is passed to the kernel.

#### NVSHMEM fails to initialize IBGDA transport due to `no active IB device that supports GPU-initiated communication` for DeepEP

When starting wide expert parallel deployments using the DeepEP kernels, container images (especially Ubuntu) that are not compiled against the Mellanox OFED drivers as recommended by NVIDIA may fail to start with the following error:

```text
/tmp/nvshmem_src/src/modules/transport/ibgda/ibgda.cpp 3888 no active IB device that supports GPU-initiated communication is found, exiting...

/tmp/nvshmem_src/src/host/transport/transport.cpp:nvshmemi_transport_init:282: init failed for transport: IBGDA
```

The default llm-d images based on RHEL UBI are not impacted. [Issue 412](https://github.com/llm-d/llm-d/issues/412) tracks updating our Ubuntu based images.

To resolve this issue in custom built images add the Mellanox OFED apt repository

```bash
wget -qO - https://www.mellanox.com/downloads/ofed/RPM-GPG-KEY-Mellanox | apt-key add -
cd /etc/apt/sources.list.d/ && wget https://linux.mellanox.com/public/repo/mlnx_ofed/24.10-0.7.0.0/ubuntu22.04/mellanox_mlnx_ofed.list
```

before installing `libibverbs-dev` or other `rdma-core-devel` packages.
