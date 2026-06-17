# llm-d on DigitalOcean Kubernetes Service (DOKS)

This document covers configuring DOKS clusters for running high performance LLM inference with llm-d.

For deployment instructions, see the [well-lit path guides](../../../guides/).

## Prerequisites

llm-d on DOKS is tested with the following configurations:

* GPU types: NVIDIA H100, NVIDIA RTX 6000 Ada, NVIDIA RTX 4000 Ada, NVIDIA L40S
* Versions: DOKS 1.33.1-do.3
* Networking: VPC-native clusters (required)

## Cluster Configuration

The DOKS cluster should be configured with the following settings:

* [GPU-enabled node pools](https://docs.digitalocean.com/products/kubernetes/details/supported-gpus/) with at least 2 GPU nodes
* [VPC-native networking](https://docs.digitalocean.com/products/kubernetes/details/features/#vpc-native-networking) (default for new clusters)
* [kubectl configured](https://docs.digitalocean.com/products/kubernetes/how-to/connect-to-cluster/) for cluster access

### GPU Driver Management

DigitalOcean automatically installs and manages GPU drivers on DOKS clusters:

* **NVIDIA Device Plugin**: Automatic installation for GPU discovery and scheduling
* **Driver Updates**: Managed alongside cluster updates
* **GPU Monitoring**: Built-in metrics collection via DCGM Exporter

Verify automatic GPU setup:

```bash
kubectl get pods -n nvidia-device-plugin-system
kubectl get nodes -o custom-columns="NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu"
```

### GPU Node Taints

DOKS GPU nodes use taints to prevent non-GPU workloads from scheduling. Add the following toleration to model server deployments:

```yaml
tolerations:
- key: "nvidia.com/gpu"
  operator: "Exists"
  effect: "NoSchedule"
```

## Deploying llm-d

Follow the [well-lit path guides](../../../guides/) to deploy llm-d workloads. Each guide includes DigitalOcean-specific steps where applicable.

## Troubleshooting

### LoadBalancer Pending or API Errors

**Error**: LoadBalancer stuck in `<pending>` state with API errors

**Cause**: DigitalOcean API rate limiting or concurrent LoadBalancer operations

**Solution**:

```bash
# Check LoadBalancer status
kubectl describe svc <service-name> -n <namespace>

# Wait for API operations to complete (typically 2-3 minutes)
# Sequential deployments avoid conflicts
```

### Pods Fail to Schedule on GPU Nodes

**Error**: `untolerated taint {nvidia.com/gpu}`

**Cause**: DOKS GPU nodes have automatic taints to prevent non-GPU workloads

**Solution**: Ensure your deployment includes the `nvidia.com/gpu` toleration. Verify it is applied:

```bash
kubectl describe pod <pod-name> -n <namespace> | grep Tolerations
# Should show: nvidia.com/gpu:NoSchedule op=Exists
```

### Gateway Not Programmed

**Error**: Gateway shows `PROGRAMMED: False`

**Solution**: Verify Istio is running and the LoadBalancer IP has been assigned:

```bash
kubectl get pods -n istio-system
kubectl get gateway -n <namespace>
```
