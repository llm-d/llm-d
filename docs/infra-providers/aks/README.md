# Deploying llm-d on Azure Kubernetes Service (AKS)

This guide provides instructions for configuring Azure Kubernetes Service (AKS) clusters to run LLM inference workloads using llm-d.

## Prerequisites

Before proceeding with this guide, ensure you have completed the following requirements:

- [client setup prerequisites](../../../guides/prereq/client-setup/README.md)
- The latest [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli?view=azure-cli-latest) with aks-preview extension installed (`az extension add --upgrade --name aks-preview`)
- `ClusterAdmin` RBAC role assigned to your user account for the target AKS cluster
- An AKS cluster. If you need to create one, refer to the [AKS quickstart guide](https://learn.microsoft.com/en-us/azure/aks/learn/quick-kubernetes-deploy-cli)
- Sufficient quota allocated for GPU VM instances in your Azure subscription

## Recommended GPU VM Configurations

The following table outlines the recommended Azure GPU VM sizes optimized for high-performance LLM inference workloads with llm-d:

| GPU Model | VM Size                                                                                                                                     | GPU Count | Memory per GPU | Total GPU Memory | RDMA over InfiniBand Support | Supported Well-Lit Paths                                                                                                                                                                                                                                                                                                                                                                     |
|-----------|---------------------------------------------------------------------------------------------------------------------------------------------|-----------|----------------|------------------|------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A100      | [Standard_NC24ads_A100_v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series?tabs=sizebasic)   | 1         | 80 GB          | 80 GB            | ❌                            | [Intelligent Inference Scheduling](../../../guides/inference-scheduling/README.md)<br>[Precise Prefix Cache Aware Routing](../../../guides/precise-prefix-cache-aware/README.md)                                                                                                                                                                                                             |
| A100      | [Standard_ND96asr_v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series?tabs=sizebasic)     | 8         | 40 GB          | 320 GB           | ✅                            | [Intelligent Inference Scheduling](../../../guides/inference-scheduling/README.md)<br>[Precise Prefix Cache Aware Routing](../../../guides/precise-prefix-cache-aware/README.md)                                                                                                                                                                                                             |
| A100      | [Standard_ND96amsr_A100_v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series?tabs=sizebasic) | 8         | 80 GB          | 640 GB           | ✅                            | [Intelligent Inference Scheduling](../../../guides/inference-scheduling/README.md)<br>[Precise Prefix Cache Aware Routing](../../../guides/precise-prefix-cache-aware/README.md)                                                                                                                                                                                                             |
| H100      | [Standard_ND96isr_H100_v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series?tabs=sizebasic)   | 8         | 80 GB          | 640 GB           | ✅                            | [Intelligent Inference Scheduling](../../../guides/inference-scheduling/README.md)<br>[Precise Prefix Cache Aware Routing](../../../guides/precise-prefix-cache-aware/README.md)<br>[P/D Disaggregation](../../../guides/pd-disaggregation/README.md) (2 nodes required with vLLM flag `--max-model-len=4500`)                                                                               |
| H200      | [Standard_ND96isr_H200_v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series?tabs=sizebasic) | 8         | 141 GB         | 1128 GB          | ✅                            | [Intelligent Inference Scheduling](../../../guides/inference-scheduling/README.md)<br>[Precise Prefix Cache Aware Routing](../../../guides/precise-prefix-cache-aware/README.md)<br>[P/D Disaggregation](../../../guides/pd-disaggregation/README.md) (2 nodes required)<br>[Wide Expert Parallelism (EP/DP) with LeaderWorkerSet](../../../guides/wide-ep-lws/README.md) (4 nodes required) |

## Cluster Configuration

GPUDirect RDMA is essential for achieving optimal performance with advanced deployment patterns such as [P/D Disaggregation](../../../guides/pd-disaggregation/README.md) and [Wide Expert Parallelism](../../../guides/wide-ep-lws/README.md). To enable GPUDirect RDMA, you must create and configure a GPU node pool with the appropriate VM size and install the required drivers.

### Creating the GPU Node Pool

Before creating your GPU node pool, you must decide on your driver installation strategy. Two options are available:

<details>
<summary>Option 1: Self-Managed Driver Installation</summary>

With this approach, you retain full control over the NVIDIA driver installation process. Create the node pool with the `--gpu-driver none` flag to prevent AKS from automatically installing NVIDIA drivers.

```bash
az aks nodepool add \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --cluster-name "${CLUSTER_NAME}" \
  --name "${NODEPOOL_NAME}" \
  --node-count "${NODEPOOL_NODE_COUNT}" \
  --node-vm-size "${NODEPOOL_VM_SIZE}" \
  --os-sku Ubuntu \
  --gpu-driver none
```

</details>

<details>
<summary>Option 2: AKS-Managed Driver Installation</summary>

With this approach, AKS handles the NVIDIA GPU driver installation automatically. Create the node pool without specifying the `--gpu-driver` parameter to use the managed driver installation.

```bash
az aks nodepool add \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --cluster-name "${CLUSTER_NAME}" \
  --name "${NODEPOOL_NAME}" \
  --node-count "${NODEPOOL_NODE_COUNT}" \
  --node-vm-size "${NODEPOOL_VM_SIZE}" \
  --os-sku Ubuntu
```

</details>

### Installing the DOCA-OFED Driver

For VM sizes that support RDMA over InfiniBand, the DOCA-OFED driver must be installed to enable it. Deploy the driver using the [Network Operator](http://github.com/Mellanox/network-operator/):

```bash
helmfile apply -f network-operator.helmfile.yaml
```

### Configuring the nvidia-peermem Kernel Module

After installing DOCA-OFED, you may need to install the NVIDIA GPU drivers and enable the `nvidia-peermem` kernel module, depending on your chosen installation method.

<details>
<summary>Option 1: Self-Managed Driver Installation</summary>

We recommend using the [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/index.html) to manage the installation of NVIDIA GPU drivers and related GPU components. The driver installation via the GPU Operator includes enabling the `nvidia-peermem` kernel module required for GPUDirect RDMA over InfiniBand.

```bash
helmfile apply -f gpu-operator.helmfile.yaml
```

</details>

<details>
<summary>Option 2: AKS-Managed Driver Installation</summary>

The GPU drivers installed by AKS do not enable the `nvidia-peermem` kernel module by default. This module is required for GPUDirect RDMA over InfiniBand. To load this module, deploy the `nvidia-peermem-reloader` DaemonSet:

```bash
# Deploy the nvidia-peermem-reloader DaemonSet
# Reference: https://github.com/Azure/aks-rdma-infiniband/blob/main/configs/nvidia-peermem-reloader/ds.yaml
kubectl apply -f https://raw.githubusercontent.com/Azure/aks-rdma-infiniband/refs/heads/main/configs/nvidia-peermem-reloader/ds.yaml
```

Subsequently, install the NVIDIA device plugin to enable GPU resource management in Kubernetes:

```bash
helmfile apply -f nvidia-device-plugin.helmfile.yaml
```

</details>

### Enabling Node Resource Interface (NRI)

AKS worker nodes enforce a default maximum locked memory limit (`ulimit -l`) of 64 KiB per container. This limit is insufficient for vLLM's NIXL connector, which require substantially higher locked memory allocations. To address this limitation, enable the Node Resource Interface (NRI) on all GPU nodes in your cluster. NRI allows the integration of plugins that can adjust maxium locked memory limit for containers.

#### Modifying the containerd Configuration

NRI must be explicitly enabled in the containerd configuration. The required configuration changes in the node's `/etc/containerd/config.toml` are as follows:

```toml
...
[plugins."io.containerd.nri.v1.nri"]
  disable = false
...
```

To apply this configuration:

1. Access each GPU node using `kubectl debug`:

```bash
kubectl debug node/<gpu-node-name> -it --image=ubuntu --profile=sysadmin -- chroot /host
```

2. Within the debug pod, edit the containerd configuration file:

```bash
vim /etc/containerd/config.toml
```

3. Add or modify the NRI configuration section as shown above.

4. Restart the containerd service to apply the changes:

```bash
systemctl restart containerd
```

5. Exit the debug pod:

```bash
exit
```

6. Repeat these steps for each GPU node in your cluster.

#### Deploying the ulimit Adjuster Plugin

After successfully enabling NRI and restarting containerd on all GPU nodes, deploy the [ulimit adjuster plugin](https://github.com/containerd/nri/tree/main/plugins/ulimit-adjuster) to automatically increase the locked memory limit for GPU workloads.

```bash
kubectl apply -k https://github.com/containerd/nri/contrib/kustomize/ulimit-adjuster
```

## Verification

After completing the configuration, verify that your cluster is properly set up for GPU workloads.

### Verifying Node Resources

Confirm that GPU and RDMA resources are correctly exposed on your nodes:

```bash
kubectl describe node <gpu-node-name>

...
Capacity:
  nvidia.com/gpu:     8
  rdma/ib:            8
...
```

> **Note:** The `nvidia.com/gpu` resource represents the number of physical GPUs available on the node, while `rdma/ib` indicates the maximum number of pods that can concurrently utilize RDMA over InfiniBand. As a best practice, each pod should request exactly one `rdma/ib` resource, independent of the number of GPUs it consumes.

## Point of Contact

- [Ernest Wong](https://github.com/chewong)
