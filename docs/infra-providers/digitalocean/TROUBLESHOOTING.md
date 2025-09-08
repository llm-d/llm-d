# DigitalOcean GPU Kubernetes Deployment Troubleshooting Guide

This document provides solutions for common issues encountered when deploying llm-d on DigitalOcean Kubernetes Service (DOKS) with GPU nodes.

## Common Issues Overview

### 1. GPU Node Scheduling Issues (nvidia.com/gpu taint)

**Problem Description**:

```text
Warning FailedScheduling ... 2 node(s) had untolerated taint {nvidia.com/gpu: }
```

**Root Cause**:

- DigitalOcean GPU nodes have `nvidia.com/gpu:NoSchedule` taint by default
- Pods lack the corresponding tolerations to handle this taint
- Results in pods unable to be scheduled on GPU nodes

**Solution**:

The `deploy-pd-disaggregation.sh` script automatically adds the required tolerations. If using manual deployment, add tolerations to your configuration:

```yaml
tolerations:
- key: nvidia.com/gpu
  operator: Exists
  effect: NoSchedule
```

**Verification**:

```bash
# Check node taints
kubectl describe nodes | grep -A 5 -B 5 nvidia.com/gpu

# Verify tolerations in running pods
kubectl get pods -n llm-d-pd -o yaml | grep -A 3 tolerations
```

### 2. NVIDIA Device Plugin Missing or Failed

**Problem Description**:

```text
Warning FailedScheduling ... 0/2 nodes are available: 2 Insufficient nvidia.com/gpu
```

**Root Cause**:

- NVIDIA Device Plugin not installed or failed to start
- GPU resources not exposed to Kubernetes scheduler
- Device plugin pods not running in `nvidia-device-plugin` namespace

**Solution**:

The `deploy-pd-disaggregation.sh` script automatically handles this by calling `setup-gpu-cluster.sh`. For manual resolution:

```bash
# Check if device plugin is running
kubectl get pods -n nvidia-device-plugin

# If missing, run the setup script
./setup-gpu-cluster.sh --force-reinstall

# Verify GPU resources are available
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}'
```

### 3. CUDA Out of Memory Errors

**Problem Description**:

```text
RuntimeError: CUDA out of memory. Tried to allocate XXX GiB
```

**Root Cause**:

- GPU memory utilization set too high
- Model size exceeds available VRAM
- Context length (`max-model-len`) too large for available memory

**Solution**:

Adjust GPU memory utilization and context length in the deployment script:

```bash
# Edit the configuration values in deploy-pd-disaggregation.sh
# Reduce gpu-memory-utilization from 0.85 to 0.7
# Reduce max-model-len from 8192 to 4096

# Or manually edit the generated configuration
args:
  - "--gpu-memory-utilization"
  - "0.7"              # Reduced from 0.85
  - "--max-model-len"
  - "4096"             # Reduced from 8192
```

### 4. Pod Resource Constraints (CPU/Memory)

**Problem Description**:

```text
Warning FailedScheduling ... 0/2 nodes are available: 2 Insufficient cpu, 2 Insufficient memory
```

**Root Cause**:

- Insufficient CPU or memory resources on nodes
- Resource requests too high for available node capacity
- Other pods consuming most of the node resources

**Solution**:

Check node capacity and adjust resource requests:

```bash
# Check node resource availability
kubectl describe nodes

# Check current resource usage
kubectl top nodes

# Adjust resource requests in the configuration (deploy-pd-disaggregation.sh creates optimized values)
resources:
  requests:
    memory: "12Gi"     # Reduced from 16Gi
    cpu: "3"           # Reduced from 4
  limits:
    memory: "16Gi"
    cpu: "4"
    nvidia.com/gpu: "1"
```

### 5. Model Download Failures

**Problem Description**:

```text
Error downloading model: 401 Unauthorized
```

**Root Cause**:

- Invalid or expired HuggingFace token
- Token lacks permissions for the specified model
- Network connectivity issues

**Solution**:

```bash
# Verify your HuggingFace token
export HF_TOKEN="your_token_here"
curl -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami

# Check if token has access to the model
curl -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/models/meta-llama/Llama-3.2-3B-Instruct

# Update the secret if needed
kubectl delete secret llm-d-hf-token -n llm-d-pd
kubectl create secret generic llm-d-hf-token \
  --from-literal=HF_TOKEN="$HF_TOKEN" \
  -n llm-d-pd
```

### 6. Gateway/Ingress Issues

**Problem Description**:

```text
connection refused when accessing the service
```

**Root Cause**:

- Istio gateway not properly configured
- Service mesh components not running
- Port forwarding issues

**Solution**:

```bash
# Check gateway status
kubectl get gateway -n llm-d-pd

# Verify Istio installation
kubectl get pods -n istio-system

# Check service endpoints
kubectl get svc -n llm-d-pd

# Test with port forwarding
kubectl port-forward -n llm-d-pd svc/infra-pd-inference-gateway-istio 8080:80

# Test the endpoint
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "meta-llama/Llama-3.2-3B-Instruct", "messages": [{"role": "user", "content": "test"}], "max_tokens": 10}'
```

### 7. Helmfile Deployment Failures

**Problem Description**:

```text
Error: failed to install chart: cannot use option --namespace and set attribute namespace
```

**Root Cause**:

- Namespace conflicts in helmfile configuration
- Incorrect helmfile syntax or template issues
- Missing dependencies
- Wrong environment specified

**Solution**:
The `deploy-pd-disaggregation.sh` script handles this automatically with static configuration. For manual deployment:

```bash
# Ensure you're in the correct directory
cd quickstart/examples/pd-disaggregation

# Check helmfile template generation for DigitalOcean environment
helmfile template -e digitalocean

# Deploy using DigitalOcean environment
export NAMESPACE=llm-d-pd
NAMESPACE=${NAMESPACE} helmfile apply -e digitalocean
```

## Diagnostic Commands

### General Cluster Health

```bash
# Check overall cluster status
kubectl get nodes
kubectl get pods --all-namespaces

# Check GPU node labeling
kubectl get nodes -l doks.digitalocean.com/gpu-brand=nvidia

# Verify GPU resources
kubectl describe nodes | grep -A 10 -B 10 nvidia.com/gpu
```

### P/D Disaggregation Specific

```bash
# Check P/D pods status
kubectl get pods -n llm-d-pd

# Check prefill pod logs
kubectl logs -n llm-d-pd -l llm-d.ai/role=prefill -c vllm

# Check decode pod logs
kubectl logs -n llm-d-pd -l llm-d.ai/role=decode -c vllm

# Check EPP (router) logs
kubectl logs -n llm-d-pd -l app.kubernetes.io/component=epp

# Check gateway status
kubectl get gateway -n llm-d-pd -o yaml
```

### Resource Monitoring

```bash
# Check resource usage
kubectl top nodes
kubectl top pods -n llm-d-pd

# Check events for scheduling issues
kubectl get events -n llm-d-pd --sort-by=.metadata.creationTimestamp

# Describe problematic pods
kubectl describe pods -n llm-d-pd
```

## Prevention Best Practices

### Before Deployment

1. **Verify Prerequisites**:

   ```bash
   # Ensure tools are installed
   which kubectl helm helmfile

   # Verify cluster connectivity
   kubectl cluster-info
   ```

2. **Check Node Resources**:

   ```bash
   # Verify sufficient GPU nodes
   kubectl get nodes -l doks.digitalocean.com/gpu-brand=nvidia

   # Check available resources
   kubectl describe nodes | grep -A 5 "Allocatable:"
   ```

3. **Validate HuggingFace Token**:

   ```bash
   # Test token access
   export HF_TOKEN="your_token"
   curl -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami
   ```

### During Deployment

1. **Monitor Deployment Progress**:

   ```bash
   # Watch pod creation
   kubectl get pods -n llm-d-pd -w

   # Monitor events
   kubectl get events -n llm-d-pd -w
   ```

2. **Use the Test Script**:

   ```bash
   # Run validation after deployment
   ./test-deployment.sh
   ```

### Post-Deployment

1. **Regular Health Checks**:

   ```bash
   # Monitor resource usage
   kubectl top nodes
   kubectl top pods -n llm-d-pd
   ```

2. **Log Monitoring**:

   ```bash
   # Check for errors in logs
   kubectl logs -n llm-d-pd -l llm-d.ai/role=prefill -c vllm --since=1h | grep -i error
   kubectl logs -n llm-d-pd -l llm-d.ai/role=decode -c vllm --since=1h | grep -i error
   ```

## Getting Help

If issues persist after following this guide:

1. **Collect Diagnostic Information**:

   ```bash
   # Generate cluster snapshot
   kubectl cluster-info dump > cluster-info.yaml

   # Export pod descriptions
   kubectl describe pods -n llm-d-pd > pod-descriptions.yaml

   # Export recent events
   kubectl get events -n llm-d-pd > events.yaml
   ```

2. **Check the Deployment Guide**: Refer to `README.md` for step-by-step deployment instructions

3. **Review Configuration**: Ensure your cluster meets the requirements in `README.md`

4. **Community Support**: Report issues with diagnostic information to the llm-d-infra project

## Success Indicators

A successful P/D disaggregation deployment should show:

1. **Pod Status**: All pods in `Running` state
2. **GPU Allocation**: Each pod allocated exactly 1 GPU
3. **Gateway**: Istio gateway in `Ready` state
4. **Inference**: Successful API responses to test requests
5. **Logs**: No error messages in pod logs
6. **Resource Usage**: Balanced CPU/memory utilization across nodes

Use `./test-deployment.sh` to validate all success criteria automatically.
