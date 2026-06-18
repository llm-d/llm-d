# Workload Autoscaling

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-cks-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-ibm-acc-gpu-vllm-x.yaml)

Traditional autoscaling indicators like resource utilization metrics (CPU/GPU) are often lagging indicators — they only reflect saturation after it has already occurred, by which point latency has spiked and requests may be failing. For LLM inference, this problem is compounded by the fact that GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an entirely unreliable signal.

Effective LLM autoscaling requires proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

This guide covers the autoscaling strategies available in llm-d. Both use the Kubernetes HPA or KEDA as the scaling primitive but differ in the use cases they target, the metrics that drive them, and the operational complexity they require.

## Prerequisites

Before choosing an autoscaling path, you must have a monitoring stack with a metrics adapter configured to expose the necessary signals to the HPA or KEDA.

### Prometheus

You must have a Prometheus instance running in your cluster. See [Prometheus Setup Guide](../../docs/resources/observability/setup.md) for guidance on setting up Prometheus. Make sure to enable TLS as WVA requires it to securely access the Prometheus API.

### Kubernetes Metrics Adapter

#### Installing KEDA (Recommended)

Follow the [Install KEDA](https://keda.sh/docs/2.20/deploy/) guide. KEDA includes a built-in metrics adapter that exposes custom and external metrics, making it the recommended choice for llm-d autoscaling. KEDA's adapter is actively maintained and supports a wide range of scaling scenarios, including scale-to-zero.

> [!NOTE]
> On OpenShift, follow the [Custom Metrics Autoscaler Operator documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/automatically-scaling-pods-with-the-custom-metrics-autoscaler-operator).

#### Installing Prometheus Adapter (Deprecated)

See the [Prometheus Adapter guide](./promadapter.md) for installation instructions. Note that the Prometheus Adapter is planned for deprecation, and it is recommended to use KEDA instead for autoscaling needs.

## Paths

### HPA + EPP Metrics

The [HPA + EPP Metrics](./README.hpa-epp.md) path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with signals emitted directly by the Endpoint Picker (EPP).

The guide demonstrates autoscaling using queue depth and running request count from EPP, but other metrics emitted by the EPP can be used depending on your scaling requirements. These signals reflect the actual state of the inference queue, enabling the HPA to scale out before users experience high latency and scale in when capacity is genuinely idle. This path requires only the standard Kubernetes HPA and the Prometheus Adapter, with no additional controllers. KEDA can be used in place of the native HPA if scale-to-zero is required and your cluster does not support the HPA scale to zero feature gate (alpha in Kubernetes 1.36).

### HPA + WVA Metrics

The [Workload Variant Autoscaler (WVA)](./README.wva.md) path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with the aggregated signal emitted by WVA: `wva_desired_replicas`.

WVA is designed for operators running multiple variants of the same model across different GPU hardware types (A100s, H100s, L4s), each with different cost and performance characteristics. WVA continuously monitors KV cache utilization, queue depth, and performance budgets to determine optimal replica counts across variants. Rather than scaling all variants equally, WVA preferentially adds capacity on the cheapest available variant and removes it from the most expensive — optimizing infrastructure cost without violating latency SLOs.

## Choosing a Path

| | [HPA + EPP Metrics](./README.hpa-epp.md) | [HPA + WVA Metrics](./README.wva.md) |
|---|---|---|
| **Best for** | Deployments on homogeneous hardware where each model scales independently | Multi-variant deployments where cost-aware capacity allocation across heterogeneous shared hardware is required |
| **Scaling signal** | EPP metrics such as queue depth and running request count | KV cache utilization, queue depth, performance budgets |
| **Cost optimization** | None — scales based on load signals only | Optimizes across variants by preferring lower-cost hardware |
| **Additional components** | None — standard Kubernetes HPA only | Requires the WVA controller |
| **Scale to zero** | Supported | Supported |
