# llm-d Alerting Runbook

This runbook provides response procedures for llm-d Prometheus alerts. Each section corresponds to an alert defined in [prometheus-alerting-rules.yaml](./prometheus-alerting-rules.yaml).

## Quick Reference

| Severity | Response Time | Escalation |
|----------|---------------|------------|
| Critical | Immediate (< 5 min) | Page on-call, notify stakeholders |
| Warning | 30 minutes | Investigate during business hours |
| Info | Next business day | Review during regular maintenance |

## Critical Alerts

### LLMDHighErrorRate

**Summary**: Platform-wide error rate exceeds 5%

**Impact**: Significant user impact - many inference requests are failing

**Diagnosis**:
```bash
# Check error distribution by type
kubectl logs -l app=epp -n ${NAMESPACE} | grep -i error | tail -50

# Check error rate by model
curl -s http://prometheus:9090/api/v1/query \
  --data-urlencode 'query=sum by(model_name, error_code) (rate(inference_model_request_error_total[5m]))'

# Check backend pod health
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/inferenceServing=true
```

**Resolution Steps**:
1. Identify which models/backends are generating errors
2. Check vLLM pod logs for OOM, model loading failures, or GPU errors
3. Verify network connectivity between scheduler and backends
4. Check if specific error codes dominate (see Error Code Reference below)
5. Consider rolling restart of affected pods if issue is transient

**Escalation**: If error rate persists >10 minutes, escalate to platform team lead

---

### LLMDModelHighErrorRate

**Summary**: Specific model error rate exceeds 10%

**Impact**: Users of this model are experiencing high failure rates

**Diagnosis**:
```bash
# Get affected model pods
kubectl get pods -n ${NAMESPACE} -l model_name=${MODEL_NAME}

# Check model-specific logs
kubectl logs -l model_name=${MODEL_NAME} -c vllm -n ${NAMESPACE} --tail=100

# Check if model is loaded
kubectl exec ${POD} -c vllm -n ${NAMESPACE} -- curl -s localhost:8200/v1/models
```

**Resolution Steps**:
1. Verify model is properly loaded on all replicas
2. Check for GPU memory issues specific to this model
3. Review model configuration (max_model_len, tensor_parallel_size)
4. Consider redeploying model pods
5. Check HuggingFace token validity if model requires authentication

---

### LLMDSchedulerDown

**Summary**: Inference scheduler (EPP) is unavailable

**Impact**: All inference requests will fail - no traffic routing possible

**Diagnosis**:
```bash
# Check EPP pod status
kubectl get pods -l app=epp -n ${NAMESPACE}

# Check EPP logs
kubectl logs -l app=epp -n ${NAMESPACE} --tail=100

# Check EPP service
kubectl get svc -l app=epp -n ${NAMESPACE}
kubectl get endpoints -l app=epp -n ${NAMESPACE}
```

**Resolution Steps**:
1. **Immediate**: Check if EPP pods are in CrashLoopBackOff
2. Review EPP logs for startup errors
3. Verify InferencePool configuration: `kubectl get inferencepool -n ${NAMESPACE} -o yaml`
4. Check Gateway status: `kubectl get gateway -n ${NAMESPACE} -o yaml`
5. If persistent, redeploy EPP: `helm upgrade --reuse-values gaie-* ...`

**Escalation**: Page on-call immediately - this is a total outage condition

---

### LLMDNoHealthyBackends

**Summary**: All vLLM backend pods are down

**Impact**: Complete service outage - no inference capacity available

**Diagnosis**:
```bash
# Check all vLLM pods
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/inferenceServing=true -o wide

# Check recent events
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | grep -i vllm

# Check node status
kubectl get nodes -o wide
```

**Resolution Steps**:
1. Identify why pods are down (OOM, node failure, GPU issues)
2. Check for cluster-wide issues (node failures, network problems)
3. Review resource quotas and limits
4. Force pod restart: `kubectl delete pods -l llm-d.ai/inferenceServing=true -n ${NAMESPACE}`
5. Scale up if capacity insufficient

**Escalation**: Page on-call immediately - this is a total outage condition

---

### LLMDExtremeTTFT

**Summary**: P99 time-to-first-token exceeds 30 seconds

**Impact**: Users are experiencing extremely slow initial response times

**Diagnosis**:
```bash
# Check prefill pod utilization
kubectl top pods -l role=prefill -n ${NAMESPACE}

# Check queue depth
curl -s http://prometheus:9090/api/v1/query \
  --data-urlencode 'query=sum by(pod) (vllm:num_requests_waiting)'

# Check prefix cache hit rate
curl -s http://prometheus:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))'
```

**Resolution Steps**:
1. Scale up prefill workers if queue depth is high
2. Check if prefix cache is warming up after restart
3. Review prompt sizes - extremely long prompts will have high TTFT
4. Verify KV cache transfer is working (for P/D disaggregation)
5. Consider enabling tiered prefix cache for better hit rates

---

## Warning Alerts

### LLMDElevatedErrorRate

**Summary**: Platform error rate above 1% (below critical threshold)

**Impact**: Some users experiencing failures - trend toward degradation

**Diagnosis**: Same as LLMDHighErrorRate

**Resolution Steps**:
1. Monitor trend - is error rate increasing or stable?
2. Identify error patterns before they escalate
3. Proactive capacity check
4. Review recent deployments or configuration changes

---

### LLMDHighLatencyP99

**Summary**: P99 request latency exceeds 10 seconds

**Impact**: Tail latency affecting user experience

**Diagnosis**:
```bash
# Check latency distribution
curl -s http://prometheus:9090/api/v1/query \
  --data-urlencode 'query=histogram_quantile(0.99, sum by(le, model_name) (rate(inference_model_request_duration_seconds_bucket[5m])))'

# Check for outlier pods
kubectl top pods -n ${NAMESPACE} --containers | sort -k4 -rn | head -10
```

**Resolution Steps**:
1. Identify which models/pods are contributing to high latency
2. Check if KV cache is saturated
3. Review batch sizes and concurrency settings
4. Consider enabling request prioritization

---

### LLMDHighTTFT

**Summary**: P99 time-to-first-token exceeds 10 seconds

**Impact**: Users waiting too long for initial response

**Resolution Steps**:
1. Scale prefill workers
2. Review prefix cache hit rates
3. Consider enabling P/D disaggregation if not already
4. Check network latency between prefill and decode workers

---

### LLMDHighPreemptions

**Summary**: Request preemptions indicate memory pressure

**Impact**: Requests being interrupted, potential quality degradation

**Diagnosis**:
```bash
# Check preemption rate
kubectl logs ${POD} -c vllm -n ${NAMESPACE} | grep -i preempt

# Check memory utilization
kubectl exec ${POD} -c vllm -n ${NAMESPACE} -- nvidia-smi
```

**Resolution Steps**:
1. Reduce `--max-num-seqs` to limit concurrent requests
2. Decrease `--gpu-memory-utilization` (default 0.9)
3. Limit `--max-model-len` if using long sequences
4. Scale out to distribute load

---

### LLMDKVCacheSaturation

**Summary**: KV cache utilization above 90%

**Impact**: Near capacity - preemptions likely if load increases

**Resolution Steps**:
1. Scale out decode workers
2. Enable tiered prefix cache to offload to CPU memory
3. Review max sequence lengths
4. Consider model quantization for smaller KV cache footprint

---

### LLMDRequestQueueBuildup

**Summary**: More than 50 requests waiting in queue

**Impact**: Increasing latency as queue grows

**Resolution Steps**:
1. Scale decode workers immediately
2. Enable request prioritization to serve interactive requests first
3. Consider rate limiting batch workloads
4. Review autoscaling thresholds

---

### LLMDLowPrefixCacheHitRate

**Summary**: Prefix cache hit rate below 30%

**Impact**: Inefficient prefix reuse - higher compute cost

**Resolution Steps**:
1. Analyze workload patterns - are prompts cacheable?
2. Increase cache size if workload has reusable prefixes
3. Enable prefix-cache-aware routing
4. Consider tiered prefix cache for larger working set

---

### LLMDGPUUnderutilization

**Summary**: GPU utilization below 30% for extended period

**Impact**: Wasted resources - cost optimization opportunity

**Resolution Steps**:
1. Consolidate workloads to fewer nodes
2. Reduce replica count
3. Review autoscaling min replicas
4. Consider right-sizing GPU types

---

## P/D Disaggregation Alerts

### LLMDPrefillDecodeImbalance

**Summary**: Prefill workers significantly busier than decode workers

**Resolution Steps**:
1. Increase prefill replica count
2. Adjust autoscaler to respond to prefill queue depth
3. Review traffic patterns - high prompt-to-output ratio?

---

### LLMDPrefillQueueSaturation

**Summary**: Prefill queue has >100 waiting requests

**Resolution Steps**:
1. Scale prefill workers immediately
2. Enable prefix caching to reduce prefill time
3. Consider request admission control

---

### LLMDDecodeKVCacheCritical

**Summary**: Decode worker KV cache at >95% utilization

**Impact**: Critical - OOM or request failures imminent

**Resolution Steps**:
1. Scale decode workers immediately
2. Reduce max concurrent requests
3. Consider emergency restart to clear cache

---

## SLO Alerts

### LLMDAvailabilitySLOBurn

**Summary**: Error budget burning faster than sustainable

**Impact**: SLO violation likely if trend continues

**Resolution Steps**:
1. Review recent changes - was there a deployment?
2. Identify root cause of errors
3. Consider rollback if recent change caused degradation
4. Increase capacity if load-related

---

### LLMDLatencySLOViolation

**Summary**: P99 latency exceeding SLO target

**Resolution Steps**:
1. Scale out to reduce per-pod load
2. Review optimization opportunities
3. Consider traffic shaping for latency-sensitive requests

---

### LLMDTTFTSLO

**Summary**: Time-to-first-token exceeding SLO target

**Resolution Steps**:
1. Scale prefill workers
2. Optimize prefix caching
3. Review P/D disaggregation configuration

---

## Infrastructure Alerts

### LLMDPodRestartLoop

**Summary**: Pod restarting frequently (>5 times/hour)

**Diagnosis**:
```bash
# Check restart reason
kubectl describe pod ${POD} -n ${NAMESPACE} | grep -A 5 "Last State"

# Check for OOM
kubectl logs ${POD} -c vllm -n ${NAMESPACE} --previous
```

**Resolution Steps**:
1. Check logs for crash reason
2. Review resource limits - OOM?
3. Check liveness/readiness probe settings
4. Review startup time vs. startup probe timeout

---

### LLMDMemoryPressure

**Summary**: Container memory at >85% of limit

**Resolution Steps**:
1. Monitor trend - is it increasing?
2. Increase memory limits if under-provisioned
3. Reduce concurrent requests to lower memory pressure
4. Review for memory leaks in long-running processes

---

### LLMDLowReplicaCount

**Summary**: Fewer than 2 healthy replicas

**Impact**: Reduced redundancy - single point of failure

**Resolution Steps**:
1. Scale up to minimum viable replica count
2. Investigate why replicas are down
3. Review autoscaling min replicas setting

---

### LLMDPodsPending

**Summary**: Pods pending for >15 minutes

**Diagnosis**:
```bash
# Check pending reason
kubectl describe pod ${POD} -n ${NAMESPACE} | grep -A 10 Events

# Check resource availability
kubectl describe nodes | grep -A 10 "Allocated resources"
```

**Resolution Steps**:
1. Check for GPU availability
2. Review node affinity/tolerations
3. Check resource quotas
4. Consider adding cluster capacity

---

## Error Code Reference

| Error Code | Description | Common Cause |
|------------|-------------|--------------|
| 500 | Internal Server Error | vLLM crash, OOM, model error |
| 502 | Bad Gateway | Backend pod down, network issue |
| 503 | Service Unavailable | No healthy backends, overloaded |
| 504 | Gateway Timeout | Request timed out, slow backend |
| 429 | Too Many Requests | Rate limited |

---

## Useful Commands

```bash
# Quick health check
kubectl get pods -n ${NAMESPACE} -o wide
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | tail -20

# Check metrics endpoints
kubectl port-forward ${VLLM_POD} 8200:8200 -n ${NAMESPACE} &
curl -s http://localhost:8200/metrics | grep vllm

# Check scheduler
kubectl port-forward svc/gaie-*-epp 9090:9090 -n ${NAMESPACE} &
curl -s http://localhost:9090/metrics | grep inference

# Force pod restart
kubectl delete pod ${POD} -n ${NAMESPACE}

# Scale deployment
kubectl scale deployment ${DEPLOYMENT} --replicas=3 -n ${NAMESPACE}
```

---

## Escalation Contacts

| Role | Contact | When to Escalate |
|------|---------|------------------|
| On-Call Engineer | PagerDuty | Critical alerts |
| Platform Team Lead | Slack #llm-d-oncall | Persistent issues >30 min |
| SRE Manager | Phone | Total outage >1 hour |

---

## Related Documentation

- [Troubleshooting Guide](../troubleshooting.md)
- [Prometheus PromQL Queries](./example-promQL-queries.md)
- [Monitoring Setup](./README.md)
- [llm-d Guides](../../guides/README.md)
