# Autoscaling Workloads with HPA and EPP Metrics

This guide explains how to configure autoscaling for LLM workloads by integrating the
Kubernetes Horizontal Pod Autoscaler (HPA) with metrics emitted by the Endpoint Picker (EPP).
By using gateway-level signals like queue size and active request counts,
you can achieve more responsive and model-aware scaling than with traditional
CPU/Memory metrics.

## Overview

Traditional autoscaling often relies on resource utilization (CPU/GPU). However, for LLM
inference, resource usage is often "pegged" at 100% during active batches, making it a poor
indicator of true load.

The llm-d architecture solves this by using the Endpoint Picker (EPP) [flow control metrics](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/#flow-control-metrics). These metrics reflect the actual state of the inference queue and the health of the model pool, allowing the HPA to scale out before users experience high latency and scale in when capacity is idle.

## Metric Definitions and Collection

Follow the [optimized-baseline](../optimized-baseline/README.md) well-lit path to set up an LLM deployment. By default, llm-d deployments include the necessary ServiceMonitors to scrape EPP metrics.

- **Metric Collection:** For details on how to ensure scraping is active, see the [EPP Metrics guide](../../docs/operations/observability/metrics.md#step-3-enable-epp-metrics).
- **Metric Definitions:** For a list of metrics emitted by EPP refer [here](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/#exposed-metrics).

### Recommended Metrics for Scaling

| Metric Name | Description | Recommended Usage |
|---|---|---|
| `llm_d_epp_flow_control_queue_size` | The number of requests currently buffered in the gateway waiting for an available backend. | Scale-out signal: High queue size indicates that the existing replicas are saturated. |
| `inference_objective_running_requests` | The number of concurrent requests being processed by the model pool. | Capacity signal: Useful for tracking total throughput. |

## Prerequisites

Make sure to enable monitoring as described in the [autoscaling prerequisites](README.md#prerequisites) section.

## Configuration Guide

### 1. Enable Flow Control in EPP

Enable the Flow Control layer by adding the `flowControl` FeatureGate to your `EndpointPickerConfig`:

```yaml
apiVersion: config.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
featureGates:
  - "flowControl"
# ...
```

Follow the [flow control configuration guide](https://gateway-api-inference-extension.sigs.k8s.io/guides/flow-control/#1-enabling-the-layer) to tune the saturation detector in your EPP deployment as needed.

### 2. Configure Prometheus Adapter Rules

Create a values file `epp-adapter-values.yaml` with the following rules:

```yaml
rules:
  external:
    - seriesQuery: 'llm_d_epp_flow_control_queue_size'
      resources:
        overrides:
          namespace:
            resource: "namespace"
          namespaced: false
      name:
        as: "epp_queue_size"
      metricsQuery: 'sum(llm_d_epp_flow_control_queue_size{inference_pool="qwen/qwen3-32b"})'
    - seriesQuery: 'inference_objective_running_requests'
      resources:
        overrides:
          namespace:
            resource: "namespace"
          namespaced: false
      name:
        as: "epp_running_requests"
      metricsQuery: 'sum(inference_objective_running_requests{top_level_controller_name="qwen/qwen3-32b-epp"})'
```

> [!NOTE]
> Replace `qwen/qwen3-32b` and `qwen/qwen3-32b-epp` with your own deployment names.

Apply the rules by upgrading the adapter:

```bash
helm upgrade prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace ${MONITORING_NAMESPACE} \
  --reuse-values \
  --values epp-adapter-values.yaml
```

Verify the metrics are visible to the Kubernetes API:

```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/default/epp_queue_size"
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/default/epp_running_requests"
```

A successful response returns a JSON object with the current metric value. A `404` means
the adapter rules are not applied correctly or the Prometheus series does not exist yet —
re-check the `metricsQuery` label values against your live Prometheus data.

### 3. Create the HPA Resource

Below is a sample HPA configuration `hpa.yaml` that uses the dual-metric setup to scale your model server based on both the queue size and current request load.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: qwen-qwen3-32b-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: qwen-qwen3-32b
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: External
    external:
      metric:
        name: epp_queue_size
      target:
        type: Value
        value: "250"
  - type: External
    external:
      metric:
        name: epp_running_requests
      target:
        type: AverageValue
        averageValue: "250"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 300 # 5 min cooldown to prevent flapping
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
```

> [!NOTE]
> The target values (`250`) used here are examples and must be tuned to your model and hardware. A good starting point is to run your model server at a known concurrency level, observe the actual metric values using `kubectl describe hpa`, and set the target below the concurrency at which your model's latency begins to degrade.

> [!NOTE]
> Although `epp_queue_size` and `epp_running_requests` originate from the EPP pod, we use `type: External` rather than `type: Pods`. This is because `type: Pods` requires metrics to come from the pods being scaled — in this case the model server pods. Since the EPP is a separate deployment acting as a gateway and emitting metrics on behalf of the model server pool, we treat its metrics as external signals.

### 4. Verify the HPA

Apply the manifest and confirm the HPA is reading metrics:

```bash
kubectl apply -f hpa.yaml
kubectl get hpa qwen-qwen3-32b-hpa -n default
```

A successful deployment would look like this:

```
NAME                          REFERENCE                            TARGETS              MINPODS   MAXPODS   REPLICAS   AGE
qwen-qwen3-32b-hpa   Deployment/qwen-qwen3-32b   0/250, 0/250 (avg)   1         3         1          5m
```

## Scale to Zero

To unlock significant cost savings on GPU resources, you can scale your deployment to zero pods when there is no traffic. With the EPP Flow Control Layer, scale-from-zero is now seamless:

- **Request Queueing:** When traffic hits a deployment with 0 replicas, the EPP flow control layer automatically queues the requests in its internal buffers.
- **Late Binding:** The EPP "holds" these requests while the autoscaler provisions the pods. Once the model server becomes ready, the EPP immediately dispatches the queued requests.
- **User Experience:** Users will see a latency spike (corresponding to the pod's startup time) but will not receive 5xx errors during the scaling event.

There are a couple of options to leverage the scale to/from zero feature.

### Option 1: Native HPA

HPA supports scaling to zero through the `HPAScaleToZero` alpha feature flag. This is the recommended path for a native Kubernetes experience.

1. **Enable Feature Gate:** Follow the [Kubernetes Alpha Feature Guide](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/) to enable the `HPAScaleToZero` feature gate on your cluster.
2. **Configure HPA:** Set `minReplicas: 0` in your HPA manifest.
3. **Outcome:** The HPA will de-provision all pods when metrics hit zero and re-provision them as soon as `epp_queue_size > 0`.

### Option 2: KEDA

If your environment does not allow alpha feature gates, KEDA is a stable alternative. **Note:** KEDA is also the recommended path forward as the Prometheus Adapter is planned for deprecation.

1. **Setup KEDA:** Install KEDA and follow the [KEDA Prometheus Scaler guide](https://keda.sh/docs/scalers/prometheus/). Note that KEDA comes with its own built-in metrics adapter that is enabled by default when you install KEDA. Unlike HPA, it does not require the Prometheus adapter installation.
2. **Configure Scaler:** Use the same `epp_queue_size` metric as a trigger.
3. **Outcome:** KEDA scales the deployment from 0 to 1 as soon as a request is queued. Once at 1 pod, the standard HPA (configured with `minReplicas: 1`) takes over to scale up to N.
