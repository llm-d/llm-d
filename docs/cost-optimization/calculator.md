# llm-d Cost Calculator

This document provides formulas and examples for estimating the total cost of ownership (TCO) for llm-d deployments.

## Table of Contents

1. [Quick Estimation](#quick-estimation)
2. [Detailed Cost Model](#detailed-cost-model)
3. [Example Calculations](#example-calculations)
4. [Cost Comparison Scenarios](#cost-comparison-scenarios)
5. [Break-Even Analysis](#break-even-analysis)

---

## Quick Estimation

### Monthly Cost Formula (Simplified)

```
Monthly_Cost = GPU_Hours × GPU_Rate × (1 + Overhead_Factor)

Where:
- GPU_Hours = Total_Pods × Hours_Per_Month × GPUs_Per_Pod
- GPU_Rate = Cloud provider GPU hourly rate
- Overhead_Factor = 0.15 to 0.25 (network, storage, CPU)
```

### Quick Reference Table

| Deployment Size | GPUs | Monthly On-Demand | Monthly Spot | Use Case |
|----------------|------|-------------------|--------------|----------|
| Development | 1-2 | $750 - $1,500 | $300 - $600 | Testing, small models |
| Small Production | 4-8 | $3,000 - $8,000 | $1,200 - $3,200 | Single model, low traffic |
| Medium Production | 16-32 | $12,000 - $32,000 | $5,000 - $13,000 | Multiple models, moderate traffic |
| Large Production | 64+ | $50,000+ | $20,000+ | High-throughput, multi-tenant |

---

## Detailed Cost Model

### 1. Compute Costs

#### GPU/Accelerator Costs

```
GPU_Cost_Monthly = Σ (Pod_Count_i × GPUs_Per_Pod_i × Hours_Running × Hourly_Rate_i)
```

**Variables:**
- `Pod_Count_i`: Number of pods of type i (prefill, decode)
- `GPUs_Per_Pod_i`: GPU count per pod configuration
- `Hours_Running`: Operational hours per month (max 730)
- `Hourly_Rate_i`: GPU hourly rate from cloud provider

**Example Rates (December 2024):**

| GPU | AWS ($/hr) | GCP ($/hr) | Azure ($/hr) |
|-----|------------|------------|--------------|
| H100 80GB | $4.15 | $3.92 | $4.50 |
| A100 80GB | $3.06 | $2.95 | $3.40 |
| A100 40GB | $1.89 | $1.97 | $2.10 |
| L40S | $1.10 | $1.05 | $1.15 |
| A10G | $0.76 | - | - |
| L4 | - | $0.40 | - |

#### CPU/Memory Costs

```
CPU_Cost_Monthly = Σ (Node_Count × vCPU_Per_Node × vCPU_Rate × Hours)
```

Typically 2-5% of GPU costs for inference workloads.

### 2. Network Costs

#### Data Transfer Costs

```
Network_Cost = Ingress_Cost + Egress_Cost + Inter_Zone_Cost

Where:
- Ingress_Cost ≈ $0 (most providers)
- Egress_Cost = Egress_GB × Egress_Rate (varies by provider)
- Inter_Zone_Cost = Cross_Zone_GB × Inter_Zone_Rate
```

**llm-d Specific Network Costs:**

| Transfer Type | Typical Volume | Cost Estimate |
|---------------|---------------|---------------|
| KV Cache Transfers (P→D) | 1-10 GB/hour per model | $0.01-0.10/GB inter-zone |
| Model Loading | 10-500 GB per model | One-time or infrequent |
| Client Traffic | Variable | Standard egress rates |

#### KV Cache Transfer Cost Formula

```
KV_Transfer_Cost = Requests_Per_Hour × Avg_KV_Size_MB × Transfer_Rate

Where:
- Avg_KV_Size_MB = (Sequence_Length × Hidden_Dim × Num_Layers × 2) / (1024 × 1024)
- Transfer_Rate = $0.01/GB inter-zone (typical)
```

### 3. Storage Costs

```
Storage_Cost_Monthly = Model_Storage + Cache_Storage + Log_Storage

Where:
- Model_Storage = Model_Size_GB × Replicas × Storage_Rate
- Cache_Storage = Cache_Size_GB × Storage_Rate (if persistent)
- Log_Storage = Log_Rate_GB_Per_Day × 30 × Storage_Rate
```

**Storage Rates (Reference):**
| Storage Type | AWS ($/GB/mo) | GCP ($/GB/mo) | Azure ($/GB/mo) |
|--------------|---------------|---------------|-----------------|
| SSD (gp3/pd-ssd) | $0.08 | $0.17 | $0.12 |
| HDD (st1/pd-standard) | $0.045 | $0.04 | $0.05 |
| Object Storage | $0.023 | $0.02 | $0.018 |

### 4. Overhead Costs

Include ancillary costs often overlooked:

```
Overhead_Cost = Monitoring_Cost + Load_Balancer_Cost + DNS_Cost + Support_Cost
```

**Typical Ranges:**
| Component | Monthly Cost |
|-----------|-------------|
| Prometheus/Grafana | $0 (self-hosted) to $200+ (managed) |
| Load Balancer | $20 - $50 + data processing |
| Cloud DNS | $0.50 per zone + queries |
| Support Plan | 3-10% of compute spend |

---

## Example Calculations

### Example 1: Small Production Deployment

**Configuration:**
- 2 prefill pods (1× H100 each)
- 4 decode pods (1× A100 80GB each)
- 100% uptime (730 hours/month)
- GCP pricing

**Calculation:**
```
GPU Cost:
  Prefill: 2 pods × 1 GPU × 730 hr × $3.92 = $5,723
  Decode:  4 pods × 1 GPU × 730 hr × $2.95 = $8,614
  Total GPU: $14,337

Network Cost (estimate):
  KV transfers: ~500 GB/month × $0.01 = $5
  Egress: ~100 GB × $0.12 = $12
  Total Network: $17

Storage Cost:
  Models: 200 GB × $0.17 = $34
  Logs: 50 GB × $0.04 = $2
  Total Storage: $36

Overhead:
  Monitoring + LB: $50

Total Monthly Cost: $14,337 + $17 + $36 + $50 = $14,440
Cost per GPU-hour: $14,337 / (6 GPUs × 730 hr) = $3.27
```

### Example 2: High-Throughput Production

**Configuration:**
- 8 prefill pods (2× H100 each, tensor parallel)
- 16 decode pods (1× A100 80GB each)
- 85% utilization (620 hours/month effective)
- AWS pricing with 1-year savings plan (30% discount)

**Calculation:**
```
GPU Cost (with discount):
  Prefill: 8 pods × 2 GPU × 730 hr × $4.15 × 0.70 = $34,005
  Decode:  16 pods × 1 GPU × 730 hr × $3.06 × 0.70 = $25,026
  Total GPU: $59,031

Network Cost:
  KV transfers: ~2 TB/month × $0.01 = $20
  Egress: ~500 GB × $0.09 = $45
  Total Network: $65

Storage Cost:
  Models: 500 GB × $0.08 = $40
  Logs: 200 GB × $0.045 = $9
  Total Storage: $49

Overhead:
  Monitoring + LB + Support (3%): $1,900

Total Monthly Cost: $59,031 + $65 + $49 + $1,900 = $61,045
Cost per GPU-hour: $59,031 / (32 GPUs × 730 hr) = $2.53
```

### Example 3: Cost-Optimized with Spot Instances

**Configuration:**
- 4 prefill pods (1× A100 40GB each) - On-demand
- 8 decode pods (1× A100 40GB each) - 50% Spot
- GCP pricing

**Calculation:**
```
GPU Cost:
  Prefill (on-demand): 4 × 1 × 730 × $1.97 = $5,752
  Decode (on-demand): 4 × 1 × 730 × $1.97 = $5,752
  Decode (spot @ 70% discount): 4 × 1 × 730 × $1.97 × 0.30 = $1,726
  Total GPU: $13,230

Total Monthly (with overhead): ~$14,000
Savings vs. all on-demand: ~$4,000 (22%)
```

---

## Cost Comparison Scenarios

### Scenario A: Monolithic vs. Disaggregated

| Configuration | Monthly Cost | TTFT P99 | Throughput |
|--------------|-------------|----------|------------|
| **Monolithic** (8× H100) | $24,250 | 450ms | 100 req/s |
| **Disaggregated** (4× H100 + 6× A100) | $22,400 | 320ms | 120 req/s |
| **Savings** | $1,850 (7.6%) | 29% better | 20% higher |

### Scenario B: With vs. Without Prefix Caching

| Configuration | Monthly Cost | Note |
|--------------|-------------|------|
| **Without caching** | $15,000 | Baseline |
| **With caching (60% hit rate)** | $12,000 | 20% compute reduction |
| **Savings** | $3,000/month | + improved latency |

### Scenario C: Autoscaler Impact

| Configuration | Avg. GPUs | Monthly Cost |
|--------------|-----------|-------------|
| **Fixed provisioning (peak)** | 16 | $35,000 |
| **With autoscaler** | 10 (avg) | $22,000 |
| **Savings** | 6 GPUs | $13,000 (37%) |

---

## Break-Even Analysis

### Cloud vs. On-Premises

**When does on-premises make sense?**

```
Break_Even_Months = On_Prem_CapEx / (Cloud_Monthly - On_Prem_OpEx)
```

**Example:**
- On-prem CapEx (8× H100 server): $300,000
- Cloud monthly (8× H100): $24,000
- On-prem OpEx (power, cooling, ops): $4,000/month

```
Break_Even = $300,000 / ($24,000 - $4,000) = 15 months
```

**Decision Factors:**
- Utilization >70%: On-premises may be cost-effective
- Utilization <50%: Cloud likely more economical
- Variable workloads: Cloud provides flexibility

### Spot/Preemptible Break-Even

**When to use spot instances:**

```
Expected_Cost = Spot_Rate × (1 + Preemption_Overhead)

Where:
- Preemption_Overhead = Preemption_Rate × Recovery_Time × Hourly_Rate
```

**Rule of Thumb:**
- Preemption rate <10%: Spot instances highly recommended
- Preemption rate 10-20%: Evaluate based on recovery cost
- Preemption rate >20%: Consider on-demand or reserved

---

## Cost Optimization ROI Calculator

### llm-d Feature Impact

| Feature | Implementation Effort | Monthly Savings (typical) | ROI Timeline |
|---------|----------------------|---------------------------|--------------|
| P/D Disaggregation | 1-2 days | 5-15% | Immediate |
| Prefix Caching | Hours | 10-25% (workload dependent) | Immediate |
| Autoscaling | 1-3 days | 20-40% | 1-2 weeks |
| Spot Instances | 1 day | 50-70% (suitable workloads) | Immediate |
| Right-sizing GPUs | 1 day | 10-30% | Immediate |

### Calculating Your ROI

```
Annual_Savings = Monthly_Baseline × Optimization_Factor × 12
Implementation_Cost = Engineering_Hours × Hourly_Rate
ROI = (Annual_Savings - Implementation_Cost) / Implementation_Cost × 100%
```

**Example:**
```
Monthly baseline: $50,000
Optimization factor: 25% reduction
Annual savings: $50,000 × 0.25 × 12 = $150,000
Implementation: 40 hours × $100 = $4,000
ROI: ($150,000 - $4,000) / $4,000 = 3,650%
```

---

## Summary

Use these formulas to:

1. **Estimate initial costs** for new deployments
2. **Compare configurations** (GPU types, P/D ratios)
3. **Calculate ROI** for optimization investments
4. **Forecast budgets** for capacity planning

For interactive capacity planning, see the [Capacity Planning Guide](./capacity-planning.md).
