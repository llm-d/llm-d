# Workload Autoscaling

Traditional autoscaling indicators like resource utilization metrics (CPU/GPU) are often lagging indicators — they only reflect saturation after it has already occurred, by which point latency has spiked and requests may be failing. For LLM inference, this problem is compounded by the fact that GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an unreliable signal entirely.

Effective LLM autoscaling requires proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

The llm-d stack provides two primary paths for workload autoscaling, both leveraging standard Kubernetes scaling primitives like the HPA and KEDA.

## Deploy

See the [workload autoscaling guide](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling) for manifests and step-by-step deployment instructions for both paths.

## Strategies

### HPA + EPP Metrics

This path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with signals emitted directly by the Endpoint Picker (EPP). By using metrics like queue depth and running request counts, the HPA can scale out before users experience high latency and scale in when capacity is genuinely idle.

* **Best for**: Deployments on homogeneous hardware where each model scales independently.
* **Signals**: EPP metrics (queue depth, running request count).
* **Components**: Standard Kubernetes HPA and Prometheus Adapter.

### Workload Variant Autoscaler (WVA)

The Workload Variant Autoscaler (WVA) is designed for operators running multiple model variants on shared, potentially heterogeneous GPU hardware. It continuously monitors KV cache utilization, queue depth, and performance budgets to determine optimal replica counts across variants (e.g., A100 vs. L4).

* **Best for**: Multi-variant deployments where cost-aware capacity allocation is required.
* **Signals**: KV cache utilization, queue depth, energy/performance budgets.
* **Components**: WVA controller and `VariantAutoscaling` CRD.

## Choosing a Path

| Feature | HPA + EPP Metrics | Workload Variant Autoscaler (WVA) |
|---|---|---|
| **Primary Goal** | Load-based scaling | Cost-optimized scaling across hardware |
| **Heterogeneous Support** | Limited | Native |
| **Complexity** | Low (standard K8s) | Medium (requires WVA controller) |
| **Scale to Zero** | Supported | Supported |
