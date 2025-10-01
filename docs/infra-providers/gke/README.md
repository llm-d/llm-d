# llm-d on Google Kubernetes Engine (GKE)

This document covers configuring GKE clusters for running high performance LLM inference with llm-d.

## Prerequisites

llm-d on GKE is tested with the following configurations:

  * Machine types: A3, A4, ct5p, ct5lp, ct6e
  * Versions: GKE 1.33.4+

For the well lit paths, we specifically recommend the following machine types:

| Path | GPU | TPU |
| --- | --- | --- |
| [Inference Scheduling](../../../guides/inference-scheduling/README.md) | Large models (13B+) with Hopper or newer (A3 or newer)<br>Small or highly quantized models (1-7B) with Ampere, L4, or newer (A2, G2, or newer) | ct5e (v5e) or newer |
| [Prefill / Decode Disaggregation](../../../guides/pd-disaggregation/README.md) | RDMA-enabled machine types (A3U, A4, or newer) | coming soon |
| [Wide Expert Parallelism](../../../guides/wide-ep-lws/README.md) | RDMA-enabled machine types (A3U, A4, or newer) | coming soon |

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

In addition, expert parallel deployments leveraging DeepEP with NVIDIA NVSHMEM will need to run their pods with `privileged: true` in order to perform GPU-initiated RDMA connections, or enable `PeerMappingOverride=1` in your NVIDIA kernel settings with a [manual GPU driver installation](https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers).

**_NOTE:_** While GDRCopy allows CPU-initiated RDMA connections, at the current time we have not measured a benefit to this configuration and instead recommend the default GPU-initiated setting. You can disable the GDRCopy warning in NVSHMEM initialization by setting the `NVSHMEM_DISABLE_GDRCOPY=1` environment variable on your container.

#### Ensuring network topology aware scheduling of pod replicas with RDMA

Select appropriate node selectors to ensure multi-host replicas are all colocated on the same network fabric. For many deployments, your A3 Ultra or newer reservation will be within a single zone. This ensures reachability but may not achieve the desired aggregate throughput.

On RDMA-enabled GKE nodes the `cloud.google.com/gce-topology-block` label identifies machines within the same fast network and can be used as the primitive for higher level orchestration to group the pods within a multi-host replica into the same RDMA network.

GKE recommends using [Topology Aware Scheduling with Kueue and LeaderWorkerSet](https://cloud.google.com/ai-hypercomputer/docs/workloads/schedule-gke-workloads-tas) for multi-host training and inference workloads.

For smaller scale expert parallel deployments (2 or 4 node replicas) we have not observed significant wins in requiring all nodes in the replica to be within the same `cloud.google.com/gce-topology-subblock`. We do recommend setting a pod affinity rule to place all pods within the same `cloud.google.com/gce-topology-block`:

```
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

```
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
