# Workload Autoscaling

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-cks-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-workload-autoscaling-ibm-acc-gpu-vllm-x.yaml)

Traditional autoscaling indicators like resource utilization metrics (CPU/GPU) are often lagging indicators — they only reflect saturation after it has already occurred, by which point latency has spiked and requests may be failing. For LLM inference, this problem is compounded by the fact that GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an entirely unreliable signal.

Effective LLM autoscaling requires proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

This guide covers the autoscaling strategies available in llm-d. Both use the Kubernetes HPA or KEDA as the scaling primitive but differ in the use cases they target, the metrics that drive them, and the operational complexity they require.

## Prerequisites

### Kubernetes Metrics Adapter

Before choosing an autoscaling path, you must have a metrics adapter configured to expose the necessary signals to the HPA or KEDA.

#### Installing KEDA (Recommended)

Follow the [Install KEDA](https://keda.sh/docs/2.20/deploy/) guide. KEDA includes a built-in metrics adapter that exposes custom and external metrics, making it the recommended choice for llm-d autoscaling. KEDA's adapter is actively maintained and supports a wide range of scaling scenarios, including scale-to-zero.

> [!NOTE]
> On OpenShift, follow the [Custom Metrics Autoscaler Operator documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/automatically-scaling-pods-with-the-custom-metrics-autoscaler-operator).

#### Installing Prometheus Adapter (Deprecated)

> [!WARNING]
> The Prometheus Adapter project is planned for deprecation ([kubernetes-sigs/prometheus-adapter#701](https://github.com/kubernetes-sigs/prometheus-adapter/issues/701)).

The Prometheus Adapter bridges Prometheus metrics to the Kubernetes External Metrics API, which the HPA uses to read EPP and WVA signals.

1. Add the Helm repository and install the adapter:

```bash
export MON_NS=monitoring

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace $MON_NS \
  --create-namespace
```

> [!NOTE]
> You must set `prometheus.url` to point to your Prometheus instance. If you are
using `kube-prometheus-stack`, the default service is `http://prometheus-operated.monitoring.svc:9090`.
Pass it at install time or in a values file:

```bash
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.url=http://prometheus-operated.monitoring.svc \
  --set prometheus.port=9090
```

2. Verify the adapter is running and can access Prometheus:

```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1"
```

## Paths

### HPA + EPP Metrics

The [HPA + EPP Metrics](./README.hpa-epp.md) path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with signals emitted directly by the Endpoint Picker (EPP).

The guide demonstrates autoscaling using queue depth and running request count from EPP, but other metrics emitted by the EPP can be used depending on your scaling requirements. These signals reflect the actual state of the inference queue, enabling the HPA to scale out before users experience high latency and scale in when capacity is genuinely idle. This path requires only the standard Kubernetes HPA and the Prometheus Adapter, with no additional controllers. KEDA can be used in place of the native HPA if scale-to-zero is required and your cluster does not support the HPA scale to zero feature gate (alpha in Kubernetes 1.36).

### HPA + WVA Metric

The [Workload Variant Autoscaler (WVA)](./README.wva.md) path integrates the Kubernetes Horizontal Pod Autoscaler (HPA) with the aggregated signal emitted by WVA: `wva_desired_replicas`.

WVA is designed for operators running multiple variants of the same model across different GPU hardware types (A100s, H100s, L4s), each with different cost and performance characteristics. WVA continuously monitors KV cache utilization, queue depth, and performance budgets to determine optimal replica counts across variants. Rather than scaling all variants equally, WVA preferentially adds capacity on the cheapest available variant and removes it from the most expensive — optimizing infrastructure cost without violating latency SLOs.

## Choosing a Path

| | [HPA + EPP Metrics](./README.hpa-epp.md) | [HPA + WVA Metric](./README.wva.md) |
|---|---|---|
| **Best for** | Deployments on homogeneous hardware where each model scales independently | Multi-variant deployments where cost-aware capacity allocation across heterogeneous shared hardware is required |
| **Scaling signal** | EPP metrics such as queue depth and running request count | KV cache utilization, queue depth, performance budgets |
| **Cost optimization** | None — scales based on load signals only | Optimizes across variants by preferring lower-cost hardware |
| **Additional components** | None — standard Kubernetes HPA only | Requires the WVA controller |
| **Scale to zero** | Supported | Supported |
