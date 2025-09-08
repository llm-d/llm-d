# llm-d-infra DigitalOcean Deployment Guide

## Prefill/Decode (P/D) Disaggregation for DigitalOcean GPU Clusters

## Overview

This script provides a **minimal Prefill/Decode (P/D) Disaggregation** deployment optimized for DigitalOcean GPU clusters.

### Architecture

```text
┌─────────────────────┐    ┌─────────────────────┐
│   Prefill Pod       │    │   Decode Pod        │
│   (GPU Node 1)      │    │   (GPU Node 2)      │
│   ┌───────────────┐ │    │   ┌───────────────┐ │
│   │   1 GPU       │ │    │   │   1 GPU       │ │
│   │   Process     │ │    │   │   Generate    │ │
│   │   Input       │ │    │   │   Output      │ │
│   └───────────────┘ │    │   └───────────────┘ │
└─────────────────────┘    └─────────────────────┘
            │                          │
            └──────────┬─────────────────┘
                      │
            ┌─────────▼─────────┐
            │   EPP (Router)    │
            │   Smart Request   │
            │   Scheduling      │
            └───────────────────┘
                      │
            ┌─────────▼─────────┐
            │  Istio Gateway    │
            │  External Access  │
            └───────────────────┘
```

**Total GPU Usage**: 2 GPUs (1 per node)

### Features

- **Minimal Deployment**: P/D separation using only 2 GPUs
- **DigitalOcean Optimized**: Automatically disables RDMA for DOKS compatibility
- **Intelligent Routing**: EPP automatically routes requests to prefill or decode pods based on request type
- **Fully Automated**: One-command complete deployment with integrated GPU setup

## Quick Start

### Prerequisites

1. **DigitalOcean Kubernetes cluster** with GPU nodes
2. **kubectl** configured and connected to your cluster
3. **HuggingFace Token** for model downloads
4. **Required tools**: kubectl, helm, helmfile

### One-Command Deployment

```bash
cd quickstart/docs/infra-providers/digitalocean
./deploy-pd-disaggregation.sh -t your_hf_token_here

# Deploy with monitoring (Prometheus + Grafana)
./deploy-pd-disaggregation.sh -t your_hf_token_here -m
```

This command automatically:

1. Checks prerequisites
2. Sets up GPU environment (installs NVIDIA Device Plugin if needed)
3. Sets up Gateway infrastructure (Istio)
4. Creates HuggingFace token secret
5. Deploys minimal P/D disaggregation using static DigitalOcean configuration
6. Optionally installs monitoring stack with P/D specific dashboards
7. Waits for deployment completion and displays status

### Verify Deployment

```bash
./test-deployment.sh
```

## Testing Inference

### Setup Port Forwarding

```bash
kubectl port-forward -n llm-d-pd svc/infra-pd-inference-gateway-istio 8080:80
```

### Send Test Request

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.2-3B-Instruct",
    "messages": [{"role": "user", "content": "Hello P/D!"}],
    "max_tokens": 50
  }'
```

### Expected Response

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "meta-llama/Llama-3.2-3B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! I'm running on P/D disaggregation..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

## Advanced Configuration

### Custom Model

To deploy a different model, edit the DigitalOcean values file:

```bash
# Edit the static configuration file
cd quickstart/examples/pd-disaggregation
vim ms-pd/digitalocean-values.yaml
```

Modify the `modelArtifacts` section:

```yaml
modelArtifacts:
  uri: "hf://your-model/name"
  size: 30Gi
  name: "your-model/name"

routing:
  modelName: your-model/name
```

### Resource Adjustment

Adjust resource limits in `ms-pd/digitalocean-values.yaml`:

```yaml
decode:
  containers:
  - resources:
      limits:
        memory: 32Gi      # Increase memory
        cpu: "8"          # Increase CPU
        nvidia.com/gpu: "1"
```

### Performance Tuning

Adjust vLLM parameters in `ms-pd/digitalocean-values.yaml`:

```yaml
decode:
  containers:
  - args:
      - "--max-model-len"
      - "16384"           # Increase context length
      - "--gpu-memory-utilization"
      - "0.9"             # Increase GPU memory utilization
```

### Manual Deployment

If you prefer manual control, you can deploy directly with helmfile:

```bash
cd quickstart/examples/pd-disaggregation

# Deploy using DigitalOcean environment
export NAMESPACE=llm-d-pd
NAMESPACE=${NAMESPACE} helmfile apply -e digitalocean

# Uninstall
NAMESPACE=${NAMESPACE} helmfile destroy -e digitalocean
```

## Monitoring

### Automatic Monitoring Setup

Deploy with built-in monitoring using the `-m` flag:

```bash
./deploy-pd-disaggregation.sh -t your_hf_token_here -m
```

This installs:

- **Prometheus**: Metrics collection with P/D specific scraping
- **Grafana**: Inference Gateway dashboard for EPP routing analysis
- **AlertManager**: Alert notifications
- **ServiceMonitors**: Automated discovery of vLLM, EPP metrics

### Manual Monitoring Setup

```bash
cd monitoring
./setup-monitoring.sh

# Uninstall monitoring
./setup-monitoring.sh -u
```

### Access Monitoring

```bash
# Grafana (Inference Gateway Dashboard)
kubectl port-forward -n llm-d-monitoring svc/prometheus-grafana 3000:80
# URL: http://localhost:3000
# Username: admin
# Password: kubectl get secret prometheus-grafana -n llm-d-monitoring -o jsonpath="{.data.admin-password}" | base64 -d

# Prometheus (Raw Metrics)
kubectl port-forward -n llm-d-monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
# URL: http://localhost:9090
```

### Key Metrics Monitored

**Inference Gateway (EPP Router):**

- KV Cache utilization analysis
- Request routing decisions and performance
- Endpoint selection and load balancing
- Queue management and optimization

**Model Service Performance:**

- vLLM prefill and decode metrics
- GPU utilization per pod role
- Request latency and throughput
- Memory usage and resource allocation

**Infrastructure:**

- DigitalOcean GPU node utilization
- Kubernetes cluster health
- Network performance between components
- Resource consumption patterns

## Troubleshooting

### Pods Stuck in Pending State

**Symptom**: Pods show `Pending` status

**Cause**: GPU nodes may not have proper labels or tolerations

**Solution**:

```bash
# Check GPU nodes
kubectl get nodes -l doks.digitalocean.com/gpu-brand=nvidia

# Check node resources
kubectl describe nodes | grep nvidia.com/gpu
```

### CUDA Out of Memory Errors

**Symptom**: `CUDA out of memory` errors

**Solution**: Reduce `gpu-memory-utilization` or `max-model-len`:

```yaml
args:
  - "--gpu-memory-utilization"
  - "0.7"             # Reduce from 0.85 to 0.7
  - "--max-model-len"
  - "4096"            # Reduce context length
```

### Inference Requests Failing

**Check Steps**:

1. Confirm pods are running:

   ```bash
   kubectl get pods -n llm-d-pd
   ```

1. Check pod logs:

   ```bash
   kubectl logs -n llm-d-pd -l llm-d.ai/role=prefill
   kubectl logs -n llm-d-pd -l llm-d.ai/role=decode
   ```

1. Check gateway status:

   ```bash
   kubectl get gateway -n llm-d-pd
   ```

## Uninstall

```bash
./deploy-pd-disaggregation.sh -u
```

This will clean up all related resources including:

- P/D disaggregation pods
- HuggingFace secrets
- Gateway infrastructure
- Istio configuration

## Performance Expectations

On a DigitalOcean 2-GPU setup:

- **Latency**: ~100-200ms (first token)
- **Throughput**: ~10-20 tokens/second (depending on model and hardware)
- **Memory Usage**: ~14-16GB VRAM per GPU
- **CPU Usage**: ~3-4 cores per pod

Actual performance will vary based on your specific model, input length, and GPU type.
