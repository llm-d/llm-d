# llm-d on DigitalOcean Kubernetes Service (DOKS)

This document covers configuring DOKS clusters for running high performance LLM inference with llm-d.

## Prerequisites

llm-d on DOKS is tested with the following configurations:

* GPU types: H100, RTX 6000 Ada, RTX 4000 Ada, L40S
* Versions: DOKS 1.28+
* Networking: VPC-native clusters (required)

## Cluster Configuration

The DOKS cluster should be configured with the following settings:

* [GPU-enabled node pools](https://docs.digitalocean.com/products/kubernetes/details/supported-gpus/) with at least 2 GPU nodes for P/D disaggregation
* [VPC-native networking](https://docs.digitalocean.com/products/kubernetes/details/networking/) (default for new clusters)
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

### Cluster Validation

Before deploying workloads, validate your cluster setup:

```bash
# Verify cluster access
kubectl cluster-info
kubectl get nodes -l doks.digitalocean.com/gpu-brand=nvidia

# Run comprehensive validation
./verify-do-prerequisites.sh
```

The validation script checks:
- DOKS cluster detection
- GPU nodes availability (minimum 2 required)
- NVIDIA Device Plugin status
- VPC-native networking configuration
- Storage classes and network policies

## Workload Configuration

### Deploy llm-d Workloads

Navigate to the appropriate guide and deploy with DigitalOcean-specific configurations:

```bash

# For inference scheduling
cd ../../../guides/inference-scheduling
helmfile apply -e digitalocean

# For P/D disaggregation
cd ../../../guides/pd-disaggregation
helmfile apply -e digitalocean

```

### GPU Configurations

Each GPU type has optimized settings in `gpu-configs/` automatically selected based on detected hardware:

#### RTX 4000 Ada (20GB VRAM)
- Memory utilization: 85% (~17GB available)
- Host memory: 32Gi limits, 16Gi requests
- Chunked prefill enabled for memory efficiency

#### RTX 6000 Ada / L40S (48GB VRAM)
- Memory utilization: 85% (~40GB available)
- Conservative host memory limits for stability
- Optimized for inference workloads

#### H100 (80GB VRAM)
- Memory utilization: 90% (~72GB available)
- Higher memory and CPU allocations
- Tensor parallel support for large models

### Networking and Storage

DOKS provides managed components for llm-d deployments:

* **VPC-native networking**: eBPF-based routing for optimal performance
* **Block Storage CSI**: `do-block-storage` storage class pre-configured
* **Load Balancers**: Managed Load Balancer integration for inference endpoints

## Monitoring (Optional)

Deploy Prometheus and Grafana for observability:

```bash
cd monitoring
./setup-monitoring.sh

# Access Grafana dashboard
kubectl port-forward -n llm-d-monitoring svc/prometheus-grafana 3000:80
```

We recommend enabling the monitoring stack to track:
- GPU utilization per deployment
- Inference request latency and throughput
- Memory usage and KV cache efficiency
- Network performance between prefill/decode pods

## Known Issues

### Pod Scheduling on GPU Nodes

**Issue**: Pods fail to schedule with `untolerated taint {nvidia.com/gpu}`

**Solution**: Ensure GPU tolerations are configured (automatically handled by llm-d deployments):

```yaml
tolerations:
- key: nvidia.com/gpu
  operator: Exists
  effect: NoSchedule
```

### VPC-Native Networking Requirements

**Issue**: Legacy DOKS clusters may experience networking limitations

**Solution**: VPC-native networking is required and enabled by default on new clusters. Legacy clusters cannot be upgraded to VPC-native.

Verify your cluster configuration:
```bash
kubectl get nodes -o jsonpath='{.items[0].metadata.labels.doks\.digitalocean\.com/vpc-native}'
```

Must return `"true"`.

### GPU Memory Management

**Issue**: CUDA out of memory errors on smaller GPU types

**Solution**: Use GPU-specific configurations which automatically adjust memory utilization based on detected hardware. Manual override available in deployment values.

## Testing and Validation

Verify deployment success:

```bash
# Check deployment status
kubectl get pods -n llm-d-pd
kubectl get gateway -n llm-d-pd

# Test inference endpoint
kubectl port-forward -n llm-d-pd svc/infra-pd-inference-gateway-istio 8080:80

curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen/Qwen2.5-3B-Instruct", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 20}'
```

Expected response includes successful P/D routing with sub-200ms latency.

For detailed configuration options and advanced setups, see the main [llm-d guides](../../../guides/).
