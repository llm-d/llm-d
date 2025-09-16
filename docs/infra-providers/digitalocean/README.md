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

## Quick Start

### Step 1: Install Prerequisites

Before deploying llm-d workloads, install the required components:

```bash
# Navigate to gateway provider prerequisites
cd guides/prereq/gateway-provider

# Install Gateway API and Inference Extension CRDs
./install-gateway-provider-dependencies.sh

# Install Istio control plane
helmfile apply -f istio.helmfile.yaml
```

### Step 2: Cluster Validation

Verify your cluster setup:

```bash
# Verify cluster access and GPU nodes
kubectl cluster-info
kubectl get nodes -l doks.digitalocean.com/gpu-brand=nvidia

# Verify components are ready
kubectl get pods -n istio-system
```

### Step 3: Deploy Workloads

Navigate to the appropriate guide and deploy with DigitalOcean-specific configurations:

```bash
# For inference scheduling
cd guides/inference-scheduling
export NAMESPACE=llm-d-inference-scheduling
helmfile apply -e digitalocean -n ${NAMESPACE}
kubectl apply -f httproute.digitalocean.yaml

# For P/D disaggregation
cd guides/pd-disaggregation
export NAMESPACE=llm-d-pd
helmfile apply -e digitalocean -n ${NAMESPACE}
kubectl apply -f httproute.digitalocean.yaml
```

**Note**: For P/D disaggregation, you may need to manually create the prefill service:

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ms-pd-prefill
  namespace: llm-d-pd
spec:
  selector:
    llm-d.ai/model: ms-pd-llm-d-modelservice
    llm-d.ai/role: prefill
  ports:
  - name: http
    port: 8000
    protocol: TCP
    targetPort: 8000
  type: ClusterIP
EOF
```

### Step 4: Testing

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

## Troubleshooting

### Common Issues

#### 1. CRD Not Found Errors During Deployment

**Error**: `resource mapping not found for name: "..." kind: "Gateway"`

**Cause**: Required CRDs not installed before deployment

**Solution**: Install CRDs before any helmfile deployment:
```bash
cd guides/prereq/gateway-provider
./install-gateway-provider-dependencies.sh
helmfile apply -f istio.helmfile.yaml
```

#### 2. LoadBalancer Pending or API Errors

**Error**: LoadBalancer stuck in `<pending>` state with API errors

**Cause**: DigitalOcean API rate limiting or concurrent LoadBalancer operations

**Solution**:
```bash
# Check LoadBalancer status
kubectl describe svc <service-name> -n <namespace>

# Wait for API operations to complete (typically 2-3 minutes)
# Sequential deployments avoid conflicts
```

#### 3. Pods Fail to Schedule on GPU Nodes

**Error**: `untolerated taint {nvidia.com/gpu}`

**Solution**: Verify GPU tolerations are automatically applied:
```bash
kubectl describe pod <pod-name> -n <namespace> | grep Tolerations
```

#### 4. Missing Prefill Service (P/D Disaggregation)

**Error**: HTTPRoute references non-existent `ms-pd-prefill` service

**Solution**: Manually create the service (see P/D deployment steps above)

#### 5. Gateway Not Programmed

**Error**: Gateway shows `PROGRAMMED: False`

**Solution**: Verify Istio is running and LoadBalancer IP is assigned:
```bash
kubectl get pods -n istio-system
kubectl get gateway -n <namespace>
```


## Cleanup

```bash
# Remove specific deployment
export NAMESPACE=llm-d-pd # or llm-d-inference-scheduling
kubectl delete -f httproute.digitalocean.yaml
helmfile destroy -e digitalocean -n ${NAMESPACE}

# Remove prerequisites (affects all deployments)
cd guides/prereq/gateway-provider
helmfile destroy -f istio.helmfile.yaml
./install-gateway-provider-dependencies.sh delete
```

For detailed configuration options and advanced setups, see the main [llm-d guides](../../../guides/).