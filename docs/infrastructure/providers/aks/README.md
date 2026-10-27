# Deploying llm-d on Azure Kubernetes Service

## Status and scope

This guide describes the AKS infrastructure that supports llm-d on
RDMA-capable Azure ND-series GPU nodes. In the Azure VM SKU naming convention,
the lower-case `r` in the capability part of the name identifies RDMA support.
Examples of this capability part include `asr`, `amsr`, and `isr`.

Examples include:

- `Standard_ND96asr_v4`;
- `Standard_ND96amsr_A100_v4`;
- `Standard_ND96isr_H100_v5`; and
- `Standard_ND96isr_H200_v5`.

Always confirm that the selected SKU supports InfiniBand in the target region.
Do not select a general-purpose GPU SKU only because it has NVIDIA GPUs.

Use these support levels:

| SKU class | Status in this guide |
| --- | --- |
| RDMA-capable ND-series SKU with the `r` marker | Target deployment class |
| `Standard_ND96isr_H100_v5` | Fully qualified reference |
| Other A100, H100, or H200 ND-series RDMA SKU | Use the procedure, then run all qualification gates |
| GB200 or GB300 rack-scale SKU | Use the procedure plus the supported ICG/ICB placement path; qualify the SKU-specific image and topology |

The same llm-d deployment pattern used by the H100 example also applies to
supported GB-series GPU nodes. Use AKS-managed GPU support, DRANET device
claims, `IPC_LOCK`, and the NCCL and NIXL validation gates. For GB-series
deployments, also use the available rack-aware placement controls.

The infrastructure procedure was qualified with this configuration:

| Component | Qualified configuration |
| --- | --- |
| AKS | Kubernetes 1.34 with `resource.k8s.io/v1` DRA APIs |
| Validated GPU node | `Standard_ND96isr_H100_v5`, eight H100 GPUs |
| Node OS | Ubuntu 24.04 |
| Minimum node image | `AKSUbuntu-2404gen2containerd-202608.14.0` |
| GPU support | AKS-managed NVIDIA driver and GPU resource registration |
| Host RDMA | Ubuntu Azure kernel inbox `mlx5` and RDMA modules |
| GPUDirect mechanism | DMA-BUF |
| NIC allocation | DRANET v1.3.0 with Kubernetes Dynamic Resource Allocation |
| Workload memory locking | `IPC_LOCK` capability |

This procedure prepares AKS for llm-d. The infrastructure tests include raw
RDMA, NCCL, and NIXL. A persistent llm-d serving deployment and an inference
request are separate application tests.

The deployment model applies to RDMA-capable ND-series GPU SKUs. The recorded
test results are specific to the qualified H100 configuration. Run the same
qualification gates for each GPU SKU, AKS node image, and driver version.

Use `AKSUbuntu-2404gen2containerd-202608.14.0` or a later Ubuntu 24.04 AKS
node image. Later node images should work with this procedure. Run the
qualification gates after each node-image update before you deploy llm-d.

## Prerequisites

You need:

- an AKS cluster;
- quota and capacity for the selected RDMA-capable ND-series GPU SKU;
- two GPU nodes for inter-node RDMA and prefill/decode tests;
- `az`, `kubectl`, `helm`, and `jq`;
- cluster administrator access; and
- a Kubernetes API server that exposes `resource.k8s.io/v1`.

Set the deployment values:

```bash
export AZURE_SUBSCRIPTION_ID='<subscription-id>'
export AZURE_RESOURCE_GROUP='<resource-group>'
export AKS_CLUSTER_NAME='<cluster-name>'
export GPU_NODE_POOL='<node-pool-name>'
export GPU_VM_SIZE='Standard_ND96isr_H100_v5'

az account set --subscription "${AZURE_SUBSCRIPTION_ID}"
az aks get-credentials \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AKS_CLUSTER_NAME}"

kubectl config current-context
```

Confirm the DRA API before you install DRANET:

```bash
kubectl api-resources --api-group=resource.k8s.io
```

The output must include these resources at `v1`:

```text
deviceclasses
resourceclaims
resourceclaimtemplates
resourceslices
```

## 1. Create the RDMA-capable ND-series node pool

Use Ubuntu 24.04 and the AKS-managed GPU stack. The following command is a
template. Review availability zones, disk settings, autoscaling, and capacity
requirements for your environment before you run it.

```bash
az aks nodepool add \
  --subscription "${AZURE_SUBSCRIPTION_ID}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --cluster-name "${AKS_CLUSTER_NAME}" \
  --name "${GPU_NODE_POOL}" \
  --node-count 2 \
  --node-vm-size "${GPU_VM_SIZE}" \
  --os-sku Ubuntu2404 \
  --enable-managed-gpu true \
  --node-taints sku=gpu:NoSchedule
```

`--enable-managed-gpu=true` selects the full AKS-managed GPU experience. AKS
then manages the NVIDIA driver, Kubernetes device plugin, DCGM metrics
exporter, and GPU health monitoring. See
[Create an AKS-managed GPU node pool](https://learn.microsoft.com/azure/aks/aks-managed-gpu-nodes).

The `sku=gpu:NoSchedule` taint is recommended to keep general workloads off
the GPU nodes. It is not an RDMA requirement. GPU workloads must include the
matching toleration.

The managed GPU experience is currently a preview feature and can require the
AKS preview extension:

```bash
az extension add --upgrade --name aks-preview
```

## 2. Validated host and GPU baseline

The H100 qualification confirmed the required baseline on
`Standard_ND96isr_H100_v5` nodes. The final conformance run used the retained
cross-pool node pair. The detailed host inspection used two nodes with the
same VM size, node image, kernel, and GPU driver.

| Item | Confirmed result |
| --- | --- |
| GPU inventory | Eight NVIDIA H100 80 GB GPUs per node |
| Kubernetes GPU resources | Eight allocatable `nvidia.com/gpu` resources per node |
| Node OS | Ubuntu 24.04.4 LTS |
| Kernel | `6.8.0-1064-azure` |
| NVIDIA driver | AKS-managed NVIDIA open kernel driver `580.159.04` |
| RDMA modules | `mlx5_core`, `mlx5_ib`, `ib_core`, and `ib_uverbs` loaded from `linux-modules-6.8.0-1064-azure` |
| InfiniBand devices | Eight workload devices, `mlx5_0` through `mlx5_7`, Active and LinkUp at 400 Gb/s NDR |
| DMA-BUF | NCCL reported `DMA-BUF is available` and used `GDRDMA` |
| Containerd NRI | Enabled with `disable = false`; `/var/run/nri/nri.sock` present |

The Kubernetes node result was:

```text
NAME                                  READY   GPU   OS                   KERNEL
aks-managedh100-48873085-vmss000007   True    8     Ubuntu 24.04.4 LTS   6.8.0-1064-azure
aks-h100xpg-32240380-vmss000001       True    8     Ubuntu 24.04.4 LTS   6.8.0-1064-azure
```

The detailed host inspection recorded this module state on both inspected
H100 hosts:

```text
mlx5_core loaded=yes package=linux-modules-6.8.0-1064-azure
mlx5_ib   loaded=yes package=linux-modules-6.8.0-1064-azure
ib_core   loaded=yes package=linux-modules-6.8.0-1064-azure
ib_uverbs loaded=yes package=linux-modules-6.8.0-1064-azure
```

The eight workload InfiniBand ports reported this pattern:

```text
mlx5_0 state=ACTIVE physical_state=LinkUp link_layer=InfiniBand rate=400 Gb/sec
...
mlx5_7 state=ACTIVE physical_state=LinkUp link_layer=InfiniBand rate=400 Gb/sec
```

NCCL confirmed the GPU-direct path:

```text
DMA-BUF is available on GPU device 0
GPU Direct RDMA Enabled for HCA 0 'mlx5_0'
Channel 00/0 ... via NET/IB/0/GDRDMA
```

The effective containerd configuration confirmed NRI support:

```text
[plugins.'io.containerd.nri.v1.nri']
  disable = false
  socket_path = '/var/run/nri/nri.sock'

/var/run/nri/nri.sock: socket, owner=root:root, mode=755
```

## 3. Install DRANET

DRANET provides Kubernetes DRA allocation and isolation for the InfiniBand
NICs. It does not install the host NIC driver.

AKS-managed DRANET support is work in progress. AKS is expected to provide a
managed DRANET solution in the future. Until that service is available, you
must install and operate DRANET in the cluster.

Create the workload namespace:

```bash
kubectl create namespace llm-d-infra \
  --dry-run=client -o yaml | kubectl apply -f -
```

Create `dranet-values.yaml`:

```yaml
image:
  repository: registry.k8s.io/networking/dranet
  tag: v1.3.0
  pullPolicy: IfNotPresent

nodeSelector:
  kubernetes.azure.com/agentpool: <node-pool-name>

args:
  cloudProviderHint: AZURE
  moveIBInterfaces: false

resources:
  requests:
    cpu: 100m
    memory: 50Mi
  limits:
    memory: 256Mi
```

`moveIBInterfaces: false` keeps the IP over InfiniBand interface on the host
and lets DRANET inject the allocated RDMA character devices.

The guide uses the AKS-provided
`kubernetes.azure.com/agentpool=<node-pool-name>` label. Custom `llm-d.ai/*`
labels are not required. If one DRANET deployment must cover multiple RDMA
node pools, an operator-managed common label is an optional way to select all
of those pools.

Install the pinned chart:

```bash
helm upgrade --install dranet \
  oci://registry.k8s.io/networking/charts/dranet \
  --version v1.3.0 \
  --namespace kube-system \
  --values dranet-values.yaml \
  --wait \
  --timeout 10m
```

Verify that DRANET runs only on the intended RDMA nodes:

```bash
kubectl get daemonset dranet -n kube-system -o wide
kubectl get pods -n kube-system \
  --selector=app.kubernetes.io/name=dranet \
  -o wide
```

## 4. Create the RDMA DeviceClass

The `DeviceClass` accepts only RDMA-capable devices that the `dra.net` driver
publishes:

```yaml
apiVersion: resource.k8s.io/v1
kind: DeviceClass
metadata:
  name: dranet.net
spec:
  selectors:
    - cel:
        expression: >-
          device.driver == "dra.net" &&
          device.attributes["dra.net"].rdma == true
```

Apply the object:

```bash
kubectl apply -f dranet-device-class.yaml
```

DRANET then publishes one `ResourceSlice` for each selected ND-series node.
The number of devices is SKU-specific. Discover and record the expected count
before you create a full-node claim. The H100 reference has eight devices.

```bash
kubectl get resourceslices.resource.k8s.io -o json | jq -r '
  .items[]
  | select(.spec.driver == "dra.net")
  | [
      .spec.nodeName,
      (.spec.devices | length),
      .spec.devices[0].attributes["azure.dra.net/vmSize"].string,
      .spec.devices[0].attributes["azure.dra.net/placementGroupId"].string
    ]
  | @tsv'
```

Expected shape:

```text
<node-1>  8  Standard_ND96isr_H100_v5  <placement-group-id>
<node-2>  8  Standard_ND96isr_H100_v5  <placement-group-id>
```

For another SKU, set the claim count to the RDMA-device count that DRANET
publishes for that SKU.

## 5. Create a full-node NIC claim template

Create one template for each VM size. This H100 example allocates all eight
InfiniBand NICs to one pod. Change the name, count, and VM-size selector for a
different RDMA-capable ND SKU:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: nd-rdma-full-node-h100
  namespace: llm-d-infra
spec:
  spec:
    devices:
      requests:
        - name: rdma-nics
          exactly:
            deviceClassName: dranet.net
            count: 8
            selectors:
              - cel:
                  expression: >-
                    device.attributes["azure.dra.net"]["vmSize"] ==
                    "Standard_ND96isr_H100_v5"
```

Apply it:

```bash
kubectl apply -f nd-rdma-full-node-h100.yaml
```

Use a smaller claim count for a partial-node experiment. Partial-node GPU and
NIC placement needs additional PCI and NUMA topology validation.

## 6. Configure llm-d model-server pods

Each full-node model-server pod requests the GPU and NIC counts for its SKU.
The following H100 example requests eight GPUs and the eight-NIC claim. It
also receives `IPC_LOCK`:

```yaml
spec:
  nodeSelector:
    kubernetes.azure.com/agentpool: <node-pool-name>
  tolerations:
    - key: sku
      operator: Equal
      value: gpu
      effect: NoSchedule
  resourceClaims:
    - name: rdma-nics
      resourceClaimTemplateName: nd-rdma-full-node-h100
  containers:
    - name: modelserver
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          add:
            - IPC_LOCK
          drop:
            - ALL
      env:
        - name: UCX_TLS
          value: rc_x,cuda_copy,cuda_ipc,self
      resources:
        requests:
          nvidia.com/gpu: 8
        limits:
          nvidia.com/gpu: 8
        claims:
          - name: rdma-nics
```

Do not add `privileged: true`. Do not set an explicit 64 GiB memory-lock
limit. The qualified test kept the normal 8 MiB `RLIMIT_MEMLOCK` value and
used `IPC_LOCK` to lock 64 MiB successfully.

For prefill/decode disaggregation, use one full GPU node for prefill and one
full GPU node for decode. Set the tensor-parallel size and resource counts for
the selected SKU. Add required pod anti-affinity so the two engines run on
different nodes.

## 7. Validate before you deploy the serving stack

GPU and ResourceSlice publication alone does not make the infrastructure
ready. Run active data-path tests on the selected nodes.

Required gates:

1. A one-NIC DRA isolation pod sees exactly one allocated `uverbs` device.
2. A full-node claim exposes the expected number of `uverbs` devices.
3. Two-node `ib_write_bw` uses InfiniBand RC transport without TCP fallback.
4. NCCL reports DMA-BUF and `GDRDMA` and completes with zero wrong values.
5. NIXL selects UCX `rc_mlx5` and transfers GPU memory with zero wrong values.
6. A full-node NCCL test uses all expected GPUs and RDMA rails.

We validated this data path on two `Standard_ND96isr_H100_v5` nodes. All
required tests passed:

| Test | Validated result |
| --- | --- |
| DRA device isolation | An unprivileged pod saw only its allocated `uverbs` device |
| Full-node DRA claim | Eight RDMA devices were visible in the pod |
| Raw InfiniBand | `ib_write_bw` used RC transport and reached 375.97 Gb/s on one rail |
| NCCL | Sixteen GPUs used all eight GDRDMA rails with zero wrong values |
| NIXL | UCX `rc_mlx5` completed a verified GPU-memory transfer with zero wrong values |
| Cross-placement-group path | Raw InfiniBand, NCCL, and NIXL passed between different placement groups |

## Placement guidance

Azure provides finer-grained topology scheduling for supported GB-series
rack-scale SKUs through Interconnect Groups, Interconnect Blocks, and
per-rack interconnect subgroups. Use this model when an llm-d workload
requires explicit rack-aware placement. For current ND-series deployments,
`placementGroupId` is diagnostic VMSS metadata. It is not proof that two
nodes are in the same physical rack.
