# Infrastructure & Environments

This section covers the hardware and software requirements for llm-d, cluster configuration, accelerator specs, and platform adaptations across diverse physical execution environments.

### [Kubernetes Infrastructure Providers](providers/README.md)
Provider-specific cluster setup notes (GKE, AKS, OpenShift, Minikube, DigitalOcean).

### [Multi-Node Serving Orchestration](multi-node.md)
Deploying multi-host inference workloads with LeaderWorkerSet (LWS) and Topology Aware Scheduling.

### [Non-Kubernetes & Bare-Metal Deployments](no-kubernetes-deployment.md)
Running the llm-d routing stack on bare metal, HPC Slurm schedulers, or Ray via file-based worker discovery.

### [Fast Internode Networking & RDMA](rdma/README.md)
Orchestrating multi-host replica topologies and RDMA networking fabrics.

### [Gateway & Ingress Resources](gateway/README.md)
Configuring ingress controllers, Gateway API, and service meshes.

---


## llm-d infrastructure

llm-d tests on the following configurations, supporting leading-edge AI accelerators:

* Kubernetes: 1.29 or newer
  * Your cluster scheduler must support placing multiple pods within the same networking domain for running multi-host inference
  * Kubernetes v1.33.0+ is recommended for complete sidecar init container support (restartPolicy: Always). If using Kubernetes v1.28.x or below, pods may get stuck in Init:0/1 state due to incomplete sidecar support.
* Recent generation datacenter-class accelerators
  * AMD MI250X or newer
  * Google TPU v5e, v6e, and newer
  * NVIDIA L4, A100, H100, H200, B200, and newer
* Fast internode networking
  * For accelerators
    * AMD Infinity Fabric, InfiniBand NICs
    * Google TPU ICI
    * NVIDIA NVLink, InfiniBand or RoCE NICs
  * For hosts and north/south traffic
    * Fast (100Gbps+ aggregate throughput) datacenter NICs
* Hosts
  * 80+ x86 or ARM cores per machine
  * 500GiB or more of memory
  * PCIe 5+

Older configurations may function, especially slightly older accelerators, but testing is best-effort.

## (Optional) vLLM container image

llm-d provides container images derived from the [vLLM upstream](https://github.com/vllm-project/vllm/tree/main/docker) that are tested with the supported hardware and have all necessary optimized libraries installed. To build and deploy with your own image, you should integrate:

* General
  * vLLM: 0.10.0 or newer
  * NIXL: 0.5.0 or newer
  * UCX: 0.19.0 or newer
* NVIDIA-specific
  * NVSHMEM: 3.3.9 or newer

llm-d guides expect a series of conventions to be followed in the vLLM image:

* General
  * At least one vLLM compatible Python version must be available (3.9 to 3.12)
    * We recommend at least 3.10+
  * Required system libraries must be bundled
    * `LD_LIBRARY_PATH` must contain all necessary system libraries for vLLM to function
  * `PATH` must contain the vLLM binary and directly invoking `vllm` should start with the correct Python environment (i.e. a virtual env)
  * The default image command (or if not specified, entrypoint) should start vLLM in a serving configuration and accept additional arguments
    * A pod with `args` should see all arguments passed to vLLM
    * A pod with `command: ["vllm", "serve"]` should override any image defaults
* Caches
  * Default compilation cache directory environment variables under a shared root path under `/tmp/cache/compile/<NAME>`
    * I.e. set `VLLM_CACHE_ROOT=/tmp/cache/compile/vllm` to ensure vLLM compiles to a temporary directory
    * Future versions of vLLM will recommend mounting a pod volume to `/tmp/cache` to mitigate restart for some caches.
  * Do not hardcode the model cache directory and model cache environment variables
    * Future versions of llm-d will provide conventions for vLLM model loading
* Hardware
  * Follow best practices for your hardware ecosystem, including:
    * Expecting to mount hardware-specific drivers and libraries from a standard host location as a value
    * Ahead Of Time (AOT) compilation of kernels
  * NVIDIA specific
    * `LD_LIBRARY_PATH` includes the `/usr/local/nvidia/lib64` directory to allow Kubernetes GPU operators to inject the appropriate driver

## Installing on a well-lit infrastructure provider

The following documentation describes llm-d tested setup for cluster infrastructure providers as well as specific deployment settings that will impact how model servers is expected to access accelerators.

* [Azure Kubernetes Service (AKS)](providers/aks/README.md)
* [DigitalOcean Kubernetes (DOKS)](providers/digitalocean/README.md)
* [Google Kubernetes Engine (GKE)](providers/gke/README.md)
* [OpenShift (OCP)](providers/openshift/README.md), [OpenShift on AWS](providers/openshift-aws/README.md)
* [minikube](providers/minikube/README.md) for single-host development

These provider configurations are tested regularly.

Please follow the provider-specific documentation to ensure your Kubernetes cluster and hardware is properly configured before continuing.

## Other providers

To add a new infrastructure provider to our well-lit paths, we request the following support:

* Documentation on configuring the platform to support one or more [well-lit path guides](../../guides/README.md#well-lit-path-guides)
* The appropriate configuration contributed to the guide to deal with provider specific variation
* An automated test environment that validates the supported guides
* At least one documented platform maintainer who responds to GitHub issues and is available for regular discussion in the llm-d slack channel `#sig-installation`.
