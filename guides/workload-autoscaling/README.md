# Workload Autoscaling

Traditional autoscaling indicators like resource utilization metrics (CPU/GPU) are often lagging indicators — they only reflect saturation after it has already occurred, by which point latency has spiked and requests may be failing. For LLM inference, this problem is compounded by the fact that GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an unreliable signal entirely.

Effective LLM autoscaling requires proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

This guide covers the autoscaling strategies available in llm-d. Both use the Kubernetes HPA or KEDA as the scaling primitive but differ in the use cases they target, the metrics that drive them, and the operational complexity they require.

## HPA + IGW Metrics

The [HPA + IGW Metrics](./README.hpa-igw.md) path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with signals emitted directly by the Inference Gateway (IGW). The guide demonstrates autoscaling using queue depth and running request counts from the Endpoint Picker (EPP), but other metrics emitted by the IGW can be used depending on your scaling requirements. These signals reflect the actual state of the inference queue, enabling the HPA to scale out before users experience high latency and scale in when capacity is genuinely idle. This path requires only the standard Kubernetes HPA and the Prometheus Adapter, with no additional controllers. KEDA can be used as an alternative to native HPA for alpha features such as scale-to-zero if your cluster does not support alpha feature gates.

## Workload Variant Autoscaler (WVA)

The [Workload Variant Autoscaler (WVA)](./README.wva.md) is designed for operators running multiple models or variants on shared GPU hardware. A **variant** is a way of serving a given model with a particular combination of hardware, runtimes, and serving approach — for example, the same model running on A100s, H100s, or L4s, each with different cost and performance characteristics. WVA continuously monitors KV cache utilization, queue depth, and configurable energy and performance budgets to determine optimal replica counts across variants. Rather than scaling all variants equally, WVA preferentially adds capacity on the cheapest available variant and removes it from the most expensive — optimizing infrastructure cost without violating latency SLOs. WVA works alongside the Kubernetes HPA: it emits optimization metrics to Prometheus, and the HPA reads those metrics and performs the actual scaling.

## Choosing a Path

| | [HPA + IGW Metrics](./README.hpa-igw.md) | [Workload Variant Autoscaler (WVA)](./README.wva.md) |
|---|---|---|
| **Best for** | Deployments on homogeneous hardware where each model scales independently | Multi-variant deployments where cost-aware capacity allocation across heterogeneous shared hardware is required |
| **Scaling signal** | IGW metrics such as queue depth and running request count | KV cache utilization, queue depth, energy and performance budgets |
| **Cost optimization** | None — scales based on load signals only | Optimizes across variants by preferring lower-cost hardware |
| **Additional components** | None — standard Kubernetes HPA only | Requires the WVA controller and `VariantAutoscaling` CRD |
| **Scale to zero** | Supported | Supported |