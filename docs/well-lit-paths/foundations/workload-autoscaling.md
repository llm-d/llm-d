# Workload Autoscaling

Traditional autoscaling indicators like resource utilization metrics (CPU/GPU) are often lagging indicators — they only reflect saturation after it has already occurred, by which point latency has spiked and requests may be failing. For LLM inference, this problem is compounded by the fact that GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an unreliable signal entirely.

Effective LLM autoscaling requires proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

The llm-d stack provides two primary paths for workload autoscaling. The EPP
path uses an HPA created and managed by KEDA. The WVA path publishes
desired-replica signals that can be consumed by an HPA or KEDA, depending on
the deployment configuration.

## Deploy

See the [workload autoscaling guide](../../../guides/workload-autoscaling) for manifests and step-by-step deployment instructions for both paths.

## Strategies

### KEDA + EPP Metrics

This path configures KEDA's Prometheus scaler with signals emitted directly by
the Endpoint Picker (EPP). By using metrics such as queue depth and running
request counts, KEDA's generated HPA reacts to queueing and active request concurrency, then reduces replicas when demand falls.

* **Best for**: Deployments on homogeneous hardware where each target model-server pool can be isolated by metrics and scaled independently.
* **Signals**: EPP metrics (queue depth, running request count).
* **Components**: KEDA and Prometheus; Prometheus Adapter is not required.

### Workload Variant Autoscaler (WVA)

The Workload Variant Autoscaler (WVA) is designed for operators running multiple model variants on shared, potentially heterogeneous GPU hardware. It continuously monitors KV cache utilization, queue depth, and performance budgets to determine optimal replica counts across variants (e.g., A100 vs. L4).

* **Best for**: Multi-variant deployments where cost-aware capacity allocation is required.
* **Signals**: KV cache utilization, queue depth, energy/performance budgets.
* **Components**: WVA controller and KEDA. WVA publishes a `wva_desired_replicas` metric for any KEDA `ScaledObject` (or HPA) annotated with `llm-d.ai/managed: "true"`, and KEDA drives the scale from it. The `VariantAutoscaling` CRD is deprecated as of llm-d 0.8.0.

## Choosing a Path

| Feature | KEDA + EPP Metrics | Workload Variant Autoscaler (WVA) |
|---|---|---|
| **Primary Goal** | Load-based scaling | Cost-optimized scaling across hardware |
| **Heterogeneous Support** | Limited | Native |
| **Complexity** | Low (requires KEDA and Prometheus) | Medium (requires WVA controller) |
| **Scale to Zero** | Supported | Supported |
