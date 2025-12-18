# Troubleshooting Guide

This guide helps diagnose and resolve common issues when deploying and operating llm-d inference stacks.

## Quick Diagnostics

Before diving into specific issues, run these commands to gather diagnostic information:

```bash
# Set your namespace
export NAMESPACE=<your-namespace>

# Check overall pod status
kubectl get pods -n ${NAMESPACE}

# Check events for recent issues
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | tail -20

# Check gateway status
kubectl get gateway -n ${NAMESPACE} -o yaml | yq '.items[].status'

# Check HTTPRoute status
kubectl get httproute -n ${NAMESPACE} -o yaml | yq '.items[].status'

# Check InferencePool status
kubectl get inferencepool.inference.networking.k8s.io -n ${NAMESPACE}
```

## Deployment Issues

### Pods Stuck in Pending State

**Symptoms:**
- Pods remain in `Pending` status
- Events show `FailedScheduling`

**Diagnosis:**
```bash
kubectl describe pod <pod-name> -n ${NAMESPACE} | grep -A 10 Events
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Insufficient GPU resources | Verify GPU availability: `kubectl get nodes -o custom-columns="NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu"` |
| Node selector mismatch | Check node labels match pod requirements |
| Resource quota exceeded | Check namespace quota: `kubectl describe resourcequota -n ${NAMESPACE}` |
| PVC not bound | Verify storage class and PVC status |

### Pods in CrashLoopBackOff

**Symptoms:**
- Pods repeatedly restart
- Status shows `CrashLoopBackOff`

**Diagnosis:**
```bash
# Check pod logs
kubectl logs <pod-name> -n ${NAMESPACE} -c vllm --previous

# Check for OOM kills
kubectl describe pod <pod-name> -n ${NAMESPACE} | grep -A 5 "Last State"
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Out of memory (OOM) | Increase memory limits or reduce model size |
| Invalid model path | Verify `modelArtifacts.uri` in values.yaml |
| HuggingFace token missing | Create `llm-d-hf-token` secret with valid token |
| GPU driver issues | Check NVIDIA driver: `kubectl exec <pod> -c vllm -- nvidia-smi` |

### Model Loading Failures

**Symptoms:**
- vLLM container starts but model fails to load
- Logs show download or memory errors

**Diagnosis:**
```bash
kubectl logs <pod-name> -n ${NAMESPACE} -c vllm | grep -i "error\|failed\|exception"
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Invalid HuggingFace token | Verify token has read access to model |
| Insufficient GPU memory | Use smaller model or enable tensor parallelism |
| Network issues | Check egress rules allow HuggingFace access |
| Disk space | Verify `/data` volume has sufficient space |

## Networking Issues

### Gateway Not Receiving Traffic

**Symptoms:**
- Requests to gateway return connection refused
- Gateway service has no external IP

**Diagnosis:**
```bash
# Check gateway status
kubectl get gateway -n ${NAMESPACE} -o yaml

# Check gateway pod logs
kubectl logs -l app=inference-gateway -n ${NAMESPACE}

# Verify service has endpoints
kubectl get endpoints -n ${NAMESPACE}
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Gateway not programmed | Check `status.conditions` for errors |
| LoadBalancer pending | Verify cloud provider LB integration |
| Missing HTTPRoute | Apply HTTPRoute for your gateway provider |
| Port mismatch | Verify service ports match HTTPRoute |

### HTTPRoute Not Working

**Symptoms:**
- Gateway is ready but requests fail
- 404 errors on inference endpoints

**Diagnosis:**
```bash
kubectl get httproute -n ${NAMESPACE} -o yaml | yq '.items[].status.parents[]'
```

**Expected status:**
```yaml
conditions:
- message: "Route was valid"
  reason: Accepted
  status: "True"
  type: Accepted
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| HTTPRoute not attached | Verify `parentRefs` points to correct gateway |
| Service name mismatch | Verify `backendRefs` service name exists |
| Namespace mismatch | HTTPRoute must be in same namespace as gateway |

### 503 Service Unavailable

**Symptoms:**
- Gateway returns 503 errors
- Intermittent failures under load

**Diagnosis:**
```bash
# Check if backends are healthy
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/inferenceServing=true

# Check inference scheduler logs
kubectl logs -l app=epp -n ${NAMESPACE}
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| No healthy backends | Wait for vLLM pods to be ready |
| Health check failures | Verify `/health` endpoint responds |
| InferencePool misconfigured | Check InferencePool selector matches pods |

## Performance Issues

### High Time to First Token (TTFT)

**Symptoms:**
- Slow initial response times
- TTFT > 1s for small prompts

**Diagnosis:**
```bash
# Check model loading status
kubectl exec <decode-pod> -c vllm -n ${NAMESPACE} -- curl -s localhost:8200/v1/models

# Monitor GPU utilization
kubectl exec <decode-pod> -c vllm -n ${NAMESPACE} -- nvidia-smi
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Cold start / model loading | Allow warm-up time after pod starts |
| KV cache not warmed | Send initial requests to prime cache |
| Suboptimal batch size | Tune `--max-num-seqs` in vLLM args |
| CPU-bound tokenization | Ensure sufficient CPU resources |

### Low Throughput

**Symptoms:**
- Requests per second below expectations
- GPU utilization < 80%

**Diagnosis:**
```bash
# Check scheduler metrics
kubectl port-forward svc/gaie-<release>-epp 9090:9090 -n ${NAMESPACE} &
curl -s http://localhost:9090/metrics | grep -E "request|queue|latency"

# Check vLLM metrics
kubectl port-forward <decode-pod> 8200:8200 -n ${NAMESPACE} &
curl -s http://localhost:8200/metrics | grep -E "num_requests|gpu_cache"
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Request queueing | Scale decode replicas |
| Memory fragmentation | Restart pods or tune `--gpu-memory-utilization` |
| Suboptimal scheduling | Enable prefix cache aware routing |
| Network bottleneck | Check UCX/NIXL configuration for PD disaggregation |

### Memory Issues

**Symptoms:**
- OOM errors in vLLM logs
- Pods killed with exit code 137

**Diagnosis:**
```bash
# Check current memory usage
kubectl exec <decode-pod> -c vllm -n ${NAMESPACE} -- nvidia-smi

# Check vLLM KV cache usage
kubectl logs <decode-pod> -c vllm -n ${NAMESPACE} | grep "KV cache"
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| KV cache too large | Reduce `--gpu-memory-utilization` (default 0.9) |
| Long sequences | Limit `--max-model-len` |
| Too many concurrent requests | Reduce `--max-num-seqs` |
| Shared memory insufficient | Increase `/dev/shm` size in deployment |

## Prefill/Decode Disaggregation Issues

### KV Cache Transfer Failures

**Symptoms:**
- Requests timeout during token generation
- Errors mentioning NIXL or KV transfer

**Diagnosis:**
```bash
# Check routing proxy logs
kubectl logs <pod-name> -c routing-proxy -n ${NAMESPACE}

# Check NIXL connection status
kubectl logs <pod-name> -c vllm -n ${NAMESPACE} | grep -i nixl
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| UCX TLS misconfigured | Verify `UCX_TLS` environment variable |
| Network policy blocking | Allow TCP traffic between prefill/decode pods |
| Side channel port blocked | Ensure port 5557 is accessible |
| RDMA not available | Fallback to TCP: `UCX_TLS=tcp` |

### Prefill/Decode Imbalance

**Symptoms:**
- Prefill pods overloaded
- Decode pods idle

**Diagnosis:**
```bash
# Compare pod metrics
kubectl top pods -n ${NAMESPACE} --containers | grep -E "prefill|decode"
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Long prompts | Add prefill replicas |
| Short outputs | Reduce decode replicas |
| Scheduler not balancing | Check EPP configuration |

## Monitoring and Observability

### Prometheus Not Scraping Metrics

**Symptoms:**
- No metrics in Grafana dashboards
- Prometheus targets show as down

**Diagnosis:**
```bash
# Check PodMonitor exists
kubectl get podmonitor -n ${NAMESPACE}

# Verify metrics endpoint
kubectl port-forward <pod> 8200:8200 -n ${NAMESPACE} &
curl -s http://localhost:8200/metrics | head -20
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| PodMonitor not deployed | Enable `monitoring.podmonitor.enabled: true` |
| Label mismatch | Verify PodMonitor selector matches pod labels |
| Port name mismatch | Check `portName` in PodMonitor matches service |

### Grafana Dashboard Empty

**Symptoms:**
- Grafana loads but shows no data
- Queries return empty results

**Diagnosis:**
```bash
# Check Prometheus is receiving data
kubectl port-forward svc/prometheus 9090:9090 -n monitoring &
# Visit http://localhost:9090/targets
```

**Common Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Wrong data source | Verify Prometheus URL in Grafana |
| Namespace filter | Check dashboard variables |
| Metrics not exposed | Verify vLLM `--enable-metrics` flag |

## Getting Help

If you've exhausted this guide:

1. **Search existing issues**: [llm-d/llm-d Issues](https://github.com/llm-d/llm-d/issues)
2. **Join Slack**: [llm-d.ai/slack](https://llm-d.ai/slack) - Ask in `#llm-d-dev`
3. **Weekly meetings**: Wednesdays 12:30 PM ET - [Public Calendar](https://red.ht/llm-d-public-calendar)
4. **File a bug**: Include diagnostic output from the Quick Diagnostics section

### Information to Include in Bug Reports

```bash
# System information
kubectl version
helm version

# llm-d deployment info
helm list -n ${NAMESPACE}
kubectl get pods -n ${NAMESPACE} -o wide

# Relevant logs (last 100 lines)
kubectl logs <pod-name> -c vllm -n ${NAMESPACE} --tail=100

# Events
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp'
```
