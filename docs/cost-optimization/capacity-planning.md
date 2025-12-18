# Capacity Planning Guide for llm-d

This guide provides methodologies for sizing llm-d deployments based on workload requirements, performance targets, and cost constraints.

## Table of Contents

1. [Introduction](#introduction)
2. [Understanding Workload Characteristics](#understanding-workload-characteristics)
3. [GPU Selection and Sizing](#gpu-selection-and-sizing)
4. [Request Volume Planning](#request-volume-planning)
5. [P/D Ratio Optimization](#pd-ratio-optimization)
6. [Peak vs. Average Load Planning](#peak-vs-average-load-planning)
7. [Multi-Model Deployments](#multi-model-deployments)
8. [Scaling Strategies](#scaling-strategies)
9. [Monitoring and Adjustment](#monitoring-and-adjustment)

---

## Introduction

Capacity planning for LLM inference requires balancing multiple factors:

- **Performance SLOs**: TTFT (Time to First Token), TBT (Time Between Tokens), throughput
- **Cost Efficiency**: GPU utilization, resource optimization
- **Reliability**: Headroom for traffic spikes, fault tolerance
- **Scalability**: Growth accommodation, elasticity

llm-d's disaggregated architecture provides unique capacity planning opportunities through independent scaling of prefill and decode phases.

---

## Understanding Workload Characteristics

### Key Metrics to Gather

Before sizing your deployment, collect these workload characteristics:

| Metric | Description | How to Measure |
|--------|-------------|----------------|
| **Input Token Length** | Average/P50/P99 prompt lengths | Analyze historical requests |
| **Output Token Length** | Average/P50/P99 generation lengths | Analyze historical responses |
| **Request Rate** | Requests per second (RPS) | Traffic logs, load testing |
| **Traffic Pattern** | Peak/valley ratios, time-of-day patterns | Historical traffic analysis |
| **Latency Requirements** | TTFT, TBT, E2E latency SLOs | Business requirements |

### Workload Categories

| Workload Type | Input Tokens | Output Tokens | Characteristics |
|---------------|-------------|---------------|-----------------|
| **Chat/Conversational** | 100-500 | 100-500 | Balanced I/O, low latency critical |
| **Summarization** | 1000-8000 | 100-500 | Long input, short output |
| **Code Generation** | 200-1000 | 500-2000 | Medium input, long output |
| **RAG/QA** | 500-4000 | 50-300 | Variable input, short output |
| **Content Generation** | 100-500 | 1000-4000 | Short input, very long output |
| **Batch Processing** | Variable | Variable | Throughput over latency |

### Workload Profiling Formula

```
Compute_Profile = (Input_Tokens × Prefill_Cost) + (Output_Tokens × Decode_Cost)

Where:
- Prefill_Cost ≈ 1 (compute-bound)
- Decode_Cost ≈ Output_Tokens × 0.1 (memory-bandwidth-bound per token)
```

---

## GPU Selection and Sizing

### GPU Capability Matrix

| GPU | VRAM | FP16 TFLOPS | Memory BW | Best For |
|-----|------|-------------|-----------|----------|
| **H100 80GB** | 80 GB | 1979 | 3.35 TB/s | Large models, high throughput |
| **A100 80GB** | 80 GB | 312 | 2.0 TB/s | Production workloads |
| **A100 40GB** | 40 GB | 312 | 1.6 TB/s | Cost-effective production |
| **L40S** | 48 GB | 362 | 864 GB/s | Smaller models, inference |
| **A10G** | 24 GB | 125 | 600 GB/s | Development, small models |

### Model-to-GPU Mapping

| Model Size | Minimum VRAM | Recommended GPU | Notes |
|------------|-------------|-----------------|-------|
| 7B parameters | 14 GB | A10G, L40S | Single GPU sufficient |
| 13B parameters | 26 GB | A100 40GB, L40S | Single GPU recommended |
| 34B parameters | 68 GB | A100 80GB | Single GPU or 2× A100 40GB |
| 70B parameters | 140 GB | 2× A100 80GB, 2× H100 | Tensor parallelism required |
| 405B parameters | 810 GB | 8× H100 | Tensor parallelism required |

### VRAM Calculation

```
Total_VRAM = Model_Weights + KV_Cache + Activation_Memory + Overhead

Where:
- Model_Weights = Parameters × Bytes_Per_Param (FP16: 2, INT8: 1, INT4: 0.5)
- KV_Cache = Batch_Size × Seq_Length × Hidden_Dim × Num_Layers × 2 × Bytes
- Activation_Memory ≈ 10-20% of model weights during inference
- Overhead ≈ 5-10% buffer
```

**Example: 70B Model with FP16**
```
Model_Weights = 70B × 2 bytes = 140 GB
KV_Cache (batch=32, seq=4096) ≈ 20-40 GB
Activation ≈ 14-28 GB
Total ≈ 174-208 GB → 2× H100 80GB or 4× A100 40GB
```

---

## Request Volume Planning

### Throughput Estimation

**Tokens Per Second (TPS) per GPU:**

| GPU | 7B Model | 13B Model | 70B Model |
|-----|----------|-----------|-----------|
| H100 | 2000-4000 | 1500-3000 | 300-800 |
| A100 80GB | 1200-2500 | 800-1800 | 200-500 |
| A100 40GB | 1000-2000 | 600-1200 | N/A (VRAM) |
| L40S | 800-1500 | 400-800 | N/A (VRAM) |

*Note: Actual throughput varies based on batch size, sequence length, and quantization.*

### GPU Count Formula

```
GPU_Count = (Target_TPS × Safety_Factor) / TPS_Per_GPU

Where:
- Target_TPS = RPS × Avg_Output_Tokens
- Safety_Factor = 1.3 to 1.5 (for headroom)
- TPS_Per_GPU = From benchmark or estimate table
```

**Example Calculation:**
```
Requirements:
- 100 RPS
- 200 average output tokens
- 70B model on A100 80GB
- Target TPS per GPU: 350

Calculation:
Target_TPS = 100 × 200 = 20,000 TPS
GPU_Count = (20,000 × 1.4) / 350 = 80 GPUs (tensor parallel pairs)
= 40 pods with 2× A100 80GB each
```

### Batch Size Optimization

| Batch Size | Latency Impact | Throughput Impact | When to Use |
|------------|----------------|-------------------|-------------|
| 1-4 | Lowest latency | Lower throughput | Real-time, latency-critical |
| 8-16 | Moderate latency | Good throughput | Interactive applications |
| 32-64 | Higher latency | High throughput | Batch processing |
| 128+ | Highest latency | Maximum throughput | Offline processing |

---

## P/D Ratio Optimization

### Understanding P/D Disaggregation

llm-d separates inference into:
- **Prefill (P)**: Process input tokens (compute-intensive)
- **Decode (D)**: Generate output tokens (memory-bandwidth-intensive)

### Ratio Guidelines by Workload

| Workload Pattern | Recommended P:D | Rationale |
|------------------|-----------------|-----------|
| Long input, short output | 3:1 to 4:1 | Heavy prefill load |
| Short input, long output | 1:2 to 1:3 | Heavy decode load |
| Balanced I/O | 1:1 to 2:1 | Equal distribution |
| High prefix cache hit | 1:2 to 1:4 | Prefill offloaded to cache |

### Dynamic Ratio Formula

```
Optimal_P:D = (Avg_Input_Tokens × Prefill_Time_Per_Token) /
              (Avg_Output_Tokens × Decode_Time_Per_Token)

With prefix caching:
Adjusted_P:D = Optimal_P:D × (1 - Cache_Hit_Rate)
```

### P/D Configuration Examples

**Summarization Workload (Long input, short output):**
```yaml
prefill:
  replicas: 6
  resources:
    nvidia.com/gpu: 2  # H100 for compute

decode:
  replicas: 2
  resources:
    nvidia.com/gpu: 1  # A100 for memory bandwidth
```

**Chat Application (Balanced):**
```yaml
prefill:
  replicas: 4
  resources:
    nvidia.com/gpu: 1

decode:
  replicas: 4
  resources:
    nvidia.com/gpu: 1
```

**Content Generation (Short input, long output):**
```yaml
prefill:
  replicas: 2
  resources:
    nvidia.com/gpu: 1

decode:
  replicas: 6
  resources:
    nvidia.com/gpu: 1
```

---

## Peak vs. Average Load Planning

### Traffic Pattern Analysis

```
Peak_Ratio = Peak_Traffic / Average_Traffic

Typical patterns:
- Business applications: 2-3× peak during business hours
- Consumer applications: 3-5× peak during evening hours
- Global applications: 1.5-2× (distributed across time zones)
```

### Capacity Planning Strategies

| Strategy | Cost | Responsiveness | Best For |
|----------|------|----------------|----------|
| **Provision for peak** | High | Immediate | SLA-critical, predictable peaks |
| **Autoscaling** | Medium | 2-10 min delay | Variable traffic, cost-conscious |
| **Hybrid (base + scale)** | Medium | Fast for base, delay for spikes | Most production workloads |
| **Queue-based** | Low | Variable | Batch, non-real-time |

### Autoscaler Configuration

```yaml
# Conservative scaling for cost optimization
autoscaler:
  minReplicas: 4          # Handle average load
  maxReplicas: 16         # Handle peak load
  metrics:
    - type: utilization
      target: 70%         # Scale up before saturation
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Percent
          value: 100      # Double capacity if needed
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300  # Wait 5min before scaling down
      policies:
        - type: Percent
          value: 25       # Scale down gradually
          periodSeconds: 120
```

### Headroom Calculation

```
Provisioned_Capacity = Average_Load × Headroom_Factor

Headroom factors:
- Minimum (risk-tolerant): 1.2× (20% headroom)
- Standard (balanced): 1.4× (40% headroom)
- Conservative (SLA-critical): 1.6× (60% headroom)
```

---

## Multi-Model Deployments

### Resource Sharing Strategies

| Strategy | Isolation | Efficiency | Complexity |
|----------|-----------|------------|------------|
| **Dedicated pools** | High | Low | Low |
| **Shared infrastructure** | Low | High | Medium |
| **Hybrid (tiered)** | Medium | Medium | Medium |

### Multi-Model Sizing

```
Total_GPUs = Σ (Model_i_GPUs × Model_i_Traffic_Share × Isolation_Factor)

Where:
- Isolation_Factor = 1.0 (shared) to 1.5 (dedicated)
```

### Example: Three-Model Deployment

**Models:**
- Model A (7B): 60% traffic, latency-critical
- Model B (13B): 30% traffic, balanced
- Model C (70B): 10% traffic, throughput-focused

**Configuration:**
```yaml
# Model A - High traffic, low latency
model_a:
  prefill_replicas: 8
  decode_replicas: 8
  gpu_type: L40S
  priority: high

# Model B - Medium traffic, balanced
model_b:
  prefill_replicas: 4
  decode_replicas: 4
  gpu_type: A100_40GB
  priority: medium

# Model C - Low traffic, batch-friendly
model_c:
  prefill_replicas: 2
  decode_replicas: 4
  gpu_type: A100_80GB
  priority: low
  spot_instances: true
```

### Request Routing for Multi-Model

```yaml
# Endpoint Picker (EPP) routing configuration
routing:
  strategy: model_affinity
  load_balancing: least_connections
  health_check_interval: 10s

  models:
    - name: model_a
      endpoints: [prefill-a-*, decode-a-*]
      weight: 60
    - name: model_b
      endpoints: [prefill-b-*, decode-b-*]
      weight: 30
    - name: model_c
      endpoints: [prefill-c-*, decode-c-*]
      weight: 10
```

---

## Scaling Strategies

### Horizontal vs. Vertical Scaling

| Approach | When to Use | Considerations |
|----------|-------------|----------------|
| **Horizontal (more pods)** | Throughput scaling, HA | Network overhead, load balancing |
| **Vertical (bigger GPUs)** | Single-request latency | Cost, availability |
| **Tensor Parallel (more GPUs/pod)** | Large models, latency | Inter-GPU communication |

### Scaling Decision Matrix

```
IF model_fits_single_gpu AND throughput_limited:
    → Scale horizontally (add pods)

IF model_requires_multi_gpu AND throughput_limited:
    → Scale horizontally (add tensor-parallel pods)

IF latency_limited AND single_request:
    → Scale vertically (faster GPUs) OR increase tensor parallelism

IF cost_limited:
    → Optimize batch size, use spot instances, right-size GPUs
```

### Gradual Rollout Strategy

```yaml
# Canary deployment for capacity changes
rollout:
  strategy: canary
  steps:
    - replicas: 1    # Test new capacity
      duration: 10m
    - replicas: 25%  # 25% traffic
      duration: 30m
    - replicas: 50%  # 50% traffic
      duration: 30m
    - replicas: 100% # Full rollout
```

---

## Monitoring and Adjustment

### Key Capacity Metrics

```promql
# GPU Utilization - target 70-85%
avg(DCGM_FI_DEV_GPU_UTIL) by (pod)

# KV Cache Usage - target <90%
avg(vllm:kv_cache_usage_perc) by (pod)

# Queue Depth - target <10 for low latency
avg(vllm:num_requests_waiting) by (pod)

# Request Latency P99
histogram_quantile(0.99, rate(inference_model_request_latency_bucket[5m]))

# Throughput (tokens/second)
sum(rate(vllm:generation_tokens_total[1m]))
```

### Capacity Alerts

```yaml
groups:
  - name: capacity_alerts
    rules:
      # High utilization - scale up needed
      - alert: HighGPUUtilization
        expr: avg(DCGM_FI_DEV_GPU_UTIL) > 85
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "GPU utilization above 85% - consider scaling up"

      # Low utilization - scale down opportunity
      - alert: LowGPUUtilization
        expr: avg(DCGM_FI_DEV_GPU_UTIL) < 30
        for: 30m
        labels:
          severity: info
        annotations:
          summary: "GPU utilization below 30% - consider scaling down"

      # KV Cache pressure
      - alert: HighKVCacheUsage
        expr: avg(vllm:kv_cache_usage_perc) > 90
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "KV Cache usage above 90% - may impact throughput"

      # Queue buildup
      - alert: HighQueueDepth
        expr: avg(vllm:num_requests_waiting) > 20
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Request queue depth high - latency impact likely"
```

### Capacity Review Checklist

Weekly review items:

- [ ] GPU utilization trends (target: 70-85%)
- [ ] P99 latency vs. SLO
- [ ] Queue depth patterns
- [ ] Traffic growth rate
- [ ] Cost per request trends
- [ ] Autoscaler activity
- [ ] Spot instance interruptions
- [ ] Cache hit rate changes

### Capacity Adjustment Workflow

```
1. Monitor: Collect metrics for 1-2 weeks
2. Analyze: Identify patterns and bottlenecks
3. Model: Update capacity calculations
4. Plan: Design configuration changes
5. Test: Validate in staging environment
6. Deploy: Gradual rollout with monitoring
7. Verify: Confirm metrics improve
8. Document: Update capacity plans
```

---

## Summary: Capacity Planning Checklist

### Initial Deployment

- [ ] Profile workload characteristics (input/output lengths, traffic patterns)
- [ ] Select appropriate GPU type for model size
- [ ] Calculate initial replica count based on throughput requirements
- [ ] Determine P/D ratio based on workload pattern
- [ ] Configure autoscaler with appropriate thresholds
- [ ] Set up monitoring and alerting
- [ ] Plan for peak traffic (headroom or autoscaling)

### Ongoing Optimization

- [ ] Review utilization metrics weekly
- [ ] Adjust P/D ratios based on observed patterns
- [ ] Optimize batch sizes for throughput/latency balance
- [ ] Evaluate spot instance opportunities
- [ ] Monitor cache hit rates and adjust strategy
- [ ] Plan capacity for traffic growth

### Related Documentation

- **[Cost Optimization Guide](./README.md)** - Cost reduction strategies
- **[Cost Calculator](./calculator.md)** - TCO estimation formulas
- **[Monitoring Documentation](../monitoring/README.md)** - Metrics and dashboards
- **[Autoscaler Proposal](../proposals/autoscaler.md)** - Variant autoscaler details
