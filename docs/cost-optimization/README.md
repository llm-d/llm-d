# Cost Optimization Guide for llm-d

This guide helps operators understand and optimize the total cost of ownership (TCO) for llm-d deployments. It covers cost drivers, optimization strategies, and practical techniques for reducing infrastructure spend while maintaining performance SLOs.

## Table of Contents

1. [Introduction](#introduction)
2. [Cost Drivers in LLM Inference](#cost-drivers-in-llm-inference)
3. [llm-d Cost Advantages](#llm-d-cost-advantages)
4. [Optimization Strategies](#optimization-strategies)
5. [Cost Monitoring](#cost-monitoring)
6. [Cloud Provider Considerations](#cloud-provider-considerations)
7. [Additional Resources](#additional-resources)

---

## Introduction

LLM inference is inherently GPU-intensive, making hardware costs the dominant factor in production deployments. llm-d is designed with cost optimization as a primary goal, achieved through:

- **P/D Disaggregation**: Independent scaling of prefill and decode workloads
- **Prefix Caching**: Reducing redundant computation through intelligent caching
- **Variant Autoscaler**: Dynamic resource allocation based on traffic patterns
- **Intelligent Request Scheduling**: Optimal routing to maximize hardware utilization

This guide provides practical strategies for operators to minimize costs while meeting latency and throughput SLOs.

---

## Cost Drivers in LLM Inference

### Primary Cost Components

| Component | Typical % of Total Cost | Description |
|-----------|------------------------|-------------|
| **GPU/Accelerator** | 70-85% | Compute hardware (NVIDIA H100, A100, L40S, etc.) |
| **Network Transfer** | 5-15% | Inter-node communication, KV cache transfers |
| **Storage** | 3-8% | Model artifacts, checkpoints, logs |
| **CPU/Memory** | 2-5% | Scheduling, preprocessing, orchestration |

### Cost Formula

The total hourly cost for an llm-d deployment can be estimated as:

```
Total_Cost = (N_prefill × GPU_cost_prefill) + (N_decode × GPU_cost_decode)
           + Network_cost + Storage_cost + Overhead
```

Where:
- `N_prefill` = Number of prefill pods
- `N_decode` = Number of decode pods
- `GPU_cost_*` = Hourly rate for GPU type used
- `Network_cost` = Data transfer costs (especially for disaggregated deployments)
- `Storage_cost` = Model storage and KV cache persistence (if enabled)

### GPU Hourly Rates (Reference)

| GPU Type | Cloud On-Demand ($/hr) | Spot/Preemptible ($/hr) | Use Case |
|----------|------------------------|-------------------------|----------|
| NVIDIA H100 80GB | $3.50 - $4.50 | $1.20 - $2.00 | High-throughput, large models |
| NVIDIA A100 80GB | $2.50 - $3.50 | $0.80 - $1.50 | Production inference |
| NVIDIA A100 40GB | $1.50 - $2.50 | $0.50 - $1.00 | Cost-effective production |
| NVIDIA L40S | $1.00 - $1.50 | $0.40 - $0.70 | Smaller models, development |
| AMD MI300X | $3.00 - $4.00 | $1.00 - $1.80 | Alternative high-performance |

*Note: Prices vary significantly by cloud provider and region. Always verify current pricing.*

---

## llm-d Cost Advantages

### 1. P/D Disaggregation

Prefill-Decode disaggregation allows independent scaling of compute-intensive (prefill) and memory-bandwidth-intensive (decode) phases:

**Cost Benefits:**
- Scale prefill workers for burst traffic without over-provisioning decode capacity
- Use different GPU types optimized for each phase
- Reduce idle GPU time during decode-heavy workloads

**Example Savings:**
```
Traditional (coupled):     8 × H100 @ $4.00/hr = $32.00/hr
Disaggregated (llm-d):     4 × H100 (prefill) + 6 × A100 (decode)
                           = $16.00 + $15.00 = $31.00/hr
                           + Better latency characteristics
```

### 2. Prefix Caching

Prefix caching reduces redundant computation for common prompt prefixes:

**Cost Benefits:**
- Reduce TTFT (Time To First Token) for repeated prefixes
- Lower GPU compute requirements for similar requests
- Enable higher throughput per GPU

**Monitoring Cache Efficiency:**
```promql
# Prefix Cache Hit Rate (target: >60% for cost savings)
sum(rate(vllm:prefix_cache_hits_total[5m])) /
sum(rate(vllm:prefix_cache_queries_total[5m]))
```

**Cost Impact Formula:**
```
Compute_Savings = Base_Prefill_Cost × Cache_Hit_Rate × Prefix_Length_Ratio
```

### 3. Variant Autoscaler

The variant autoscaler dynamically adjusts the mix of accelerator topologies:

**Cost Benefits:**
- Automatic scale-down during low-traffic periods
- Optimal topology selection for current workload
- Prevention of over-provisioning

**Key Autoscaler Goals:**
1. Maximize SLO attainment with least hardware cost
2. Support heterogeneous accelerator mixes
3. Handle dynamic traffic patterns without manual intervention

### 4. Intelligent Request Scheduling

The Endpoint Picker (EPP) optimizes request routing:

**Cost Benefits:**
- Maximize GPU utilization across pods
- Reduce request queuing and wasted compute cycles
- Balance load to avoid hot spots

**Utilization Target:**
```promql
# GPU Utilization (target: 70-85% for cost efficiency)
avg by(gpu, node) (DCGM_FI_DEV_GPU_UTIL or nvidia_gpu_duty_cycle)
```

---

## Optimization Strategies

### Strategy 1: Right-Size GPU Selection

Match GPU capabilities to model and workload requirements:

| Model Size | Recommended GPU | Rationale |
|------------|----------------|-----------|
| <7B parameters | L40S, A10G | Cost-effective for smaller models |
| 7B-13B parameters | A100 40GB | Balance of cost and performance |
| 13B-70B parameters | A100 80GB | Sufficient VRAM for larger models |
| >70B parameters | H100, MI300X | Maximum performance and memory |

**Decision Factors:**
- Model parameter count and memory requirements
- Expected batch sizes and sequence lengths
- Latency SLO requirements
- Cost constraints

### Strategy 2: Optimize P/D Ratio

Find the optimal ratio of prefill to decode workers:

```yaml
# Example: High-throughput, short outputs
prefill:
  replicas: 4
  resources:
    nvidia.com/gpu: 1  # H100

decode:
  replicas: 2
  resources:
    nvidia.com/gpu: 1  # A100 (memory-optimized)
```

**Ratio Guidelines:**

| Workload Type | P:D Ratio | Use Case |
|---------------|-----------|----------|
| Long inputs, short outputs | 3:1 | Summarization, extraction |
| Short inputs, long outputs | 1:2 | Generation, chat |
| Balanced | 1:1 | General-purpose |

### Strategy 3: Leverage Spot/Preemptible Instances

For non-critical or burst workloads:

**Suitable Workloads:**
- Development and testing
- Batch processing jobs
- Overflow capacity during traffic spikes

**Cost Savings:** 50-70% compared to on-demand pricing

**Implementation:**
- Configure node pools with preemptible/spot VMs
- Use llm-d's graceful shutdown handling
- Implement request retry logic for preemption events

### Strategy 4: Maximize Cache Hit Rate

Optimize prefix caching for cost reduction:

**Techniques:**
1. **Prompt Engineering**: Structure prompts with common prefixes
2. **Request Batching**: Group similar requests to maximize cache reuse
3. **Cache Hierarchy Tuning**: Configure HBM → Host Memory → Remote cache tiers

**Configuration Example:**
```yaml
# Maximize prefix cache utilization
prefill:
  extraArgs:
    - "--enable-prefix-caching"
    - "--prefix-cache-max-size=80%"
```

### Strategy 5: Configure Autoscaler Policies

Tune autoscaler for cost optimization:

```yaml
# Conservative scaling for cost optimization
autoscaler:
  scaleDownDelay: 10m          # Wait before scaling down
  scaleUpThreshold: 0.8         # Scale up at 80% utilization
  scaleDownThreshold: 0.3       # Scale down below 30% utilization
  minReplicas: 1                # Minimum pods to maintain
  maxReplicas: 10               # Cost cap
```

### Strategy 6: Implement Request Prioritization

Prioritize requests based on business value:

**Priority Classes:**
| Priority | SLO Target | Billing | Use Case |
|----------|-----------|---------|----------|
| Premium | P99 < 500ms | Higher | Real-time applications |
| Standard | P99 < 2s | Normal | Interactive users |
| Batch | Best-effort | Lower | Background processing |

---

## Cost Monitoring

### Key Cost Metrics

Monitor these metrics to track cost efficiency:

```promql
# Requests per GPU-hour (efficiency metric)
sum(rate(inference_model_request_total[1h])) /
count(count by(pod) (vllm:num_requests_running))

# Tokens generated per GPU-hour
sum(increase(vllm:generation_tokens_total[1h])) /
count(count by(pod) (vllm:num_requests_running))

# KV Cache Utilization (resource efficiency)
avg(vllm:kv_cache_usage_perc) * 100

# GPU Idle Time (cost waste indicator)
1 - avg(rate(vllm:iteration_tokens_total[5m]) > 0)
```

### Cost Efficiency Dashboard

Create a Grafana dashboard with these panels:

1. **Cost per 1M Tokens** - Track unit economics
2. **GPU Utilization Heatmap** - Identify underutilized resources
3. **Request Throughput vs. Pod Count** - Measure scaling efficiency
4. **Cache Hit Rate Trend** - Monitor caching effectiveness
5. **SLO Attainment vs. Cost** - Balance quality and spend

### Alerting Rules

Configure alerts for cost anomalies:

```yaml
# Alert: Low GPU utilization (potential over-provisioning)
- alert: LowGPUUtilization
  expr: avg(DCGM_FI_DEV_GPU_UTIL) < 30
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "GPU utilization below 30% for 30 minutes"
    description: "Consider scaling down or consolidating workloads"

# Alert: Low cache hit rate (missed optimization opportunity)
- alert: LowCacheHitRate
  expr: |
    sum(rate(vllm:prefix_cache_hits_total[1h])) /
    sum(rate(vllm:prefix_cache_queries_total[1h])) < 0.4
  for: 1h
  labels:
    severity: info
  annotations:
    summary: "Prefix cache hit rate below 40%"
    description: "Review prompt patterns for caching opportunities"
```

---

## Cloud Provider Considerations

### AWS

**Cost Optimization Options:**
- **Savings Plans**: Up to 72% savings with 1-3 year commitments
- **Spot Instances**: Up to 90% savings for fault-tolerant workloads
- **Reserved Instances**: Predictable costs for steady-state workloads

**Recommended Instance Types:**
- p5.48xlarge (8× H100) for high-throughput
- p4d.24xlarge (8× A100) for production
- g5.xlarge (1× A10G) for development

### GCP

**Cost Optimization Options:**
- **Committed Use Discounts**: Up to 57% savings
- **Spot VMs**: Up to 91% savings
- **Managed Prometheus**: Avoid separate monitoring infrastructure costs

**Recommended Machine Types:**
- a3-highgpu-8g (8× H100) for maximum performance
- a2-highgpu-4g (4× A100) for production
- g2-standard-8 (1× L4) for cost-sensitive deployments

### Azure

**Cost Optimization Options:**
- **Azure Reservations**: Up to 72% savings
- **Spot VMs**: Variable savings
- **Azure Hybrid Benefit**: Combine with existing licenses

**Recommended VM Sizes:**
- Standard_ND96isr_H100_v5 (8× H100)
- Standard_NC96ads_A100_v4 (4× A100)
- Standard_NC8as_T4_v3 for development

### On-Premises / Bare Metal

**Cost Considerations:**
- **CapEx vs. OpEx**: Higher upfront cost, lower long-term TCO
- **Utilization Target**: >80% to justify ownership costs
- **Maintenance**: Factor in ops team costs and hardware refresh cycles

---

## Additional Resources

- **[Capacity Planning Guide](./capacity-planning.md)** - Sizing guidance for production deployments
- **[Cost Calculator](./calculator.md)** - Interactive formulas for TCO estimation
- **[Monitoring Documentation](../monitoring/README.md)** - Prometheus/Grafana setup
- **[Autoscaler Proposal](../proposals/autoscaler.md)** - Technical details on cost-aware scaling

---

## Summary: Cost Optimization Checklist

- [ ] Select appropriate GPU types for model size and workload
- [ ] Configure P/D disaggregation ratios based on traffic patterns
- [ ] Enable and tune prefix caching for common prompts
- [ ] Set up autoscaler with cost-aware policies
- [ ] Consider spot/preemptible instances for appropriate workloads
- [ ] Monitor GPU utilization and scale down underutilized resources
- [ ] Track cost metrics in Grafana dashboards
- [ ] Review cloud provider commitment options for predictable workloads
- [ ] Implement request prioritization for tiered service offerings
- [ ] Regularly review and adjust based on cost metrics
