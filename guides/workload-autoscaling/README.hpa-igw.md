# Autoscaling Workloads with HPA and IGW Metrics

This guide explains how to configure autoscaling for LLM workloads by integrating the
Kubernetes Horizontal Pod Autoscaler (HPA) with metrics emitted by the Inference
Gateway (IGW). By using gateway-level signals like queue depth and active request counts,
you can achieve more responsive and model-aware scaling than with traditional
CPU/Memory metrics.

## Overview

Traditional autoscaling often relies on resource utilization (CPU/GPU). However, for LLM
inference, resource usage is often "pegged" at 100% during active batches, making it a poor
indicator of true load.

The llm-d architecture solves this by using the Endpoint Picker (EPP) [flow control metrics](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/#flow-control-metrics). These metrics reflect the actual state of the inference queue and the health of the model pool, allowing the HPA to scale out before users experience high latency and scale in when capacity is idle.

## Metric Definitions and Collection

Follow the [Intelligent Inference Scheduling](https://github.com/llm-d/llm-d/blob/main/guides/inference-scheduling/README.md) well-lit path to set up an LLM deployment. By default, llm-d deployments include the necessary ServiceMonitors to scrape EPP metrics.

- **Metric Collection:** For details on how to ensure scraping is active, see the [llm-d Monitoring Guide](https://github.com/llm-d/llm-d/blob/main/docs/monitoring/README.md#epp-endpoint-picker-metrics).
- **Metric Definitions:** For a list of metrics emitted by EPP refer [here](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/#exposed-metrics).

### Recommended Metrics for Scaling

| Metric Name | Description | Recommended Usage |
|---|---|---|
| `inference_extension_flow_control_queue_size` | The number of requests currently buffered in the gateway waiting for an available backend. | Scale-out signal: High queue size indicates that the existing replicas are saturated. |
| `inference_objective_running_requests` | The number of concurrent requests being processed by the model pool. | Capacity signal: Useful for tracking total throughput. |

## Configuration Guide

### 1. Enable Flow Control in IGW

Enable the Flow Control layer by adding the `flowControl` FeatureGate to your `EndpointPickerConfig`:

```yaml
apiVersion: config.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
featureGates:
  - "flowControl"
# ...
```

Follow the [flow control configuration guide](https://gateway-api-inference-extension.sigs.k8s.io/guides/flow-control/#1-enabling-the-layer) to tune the saturation detector in your EPP deployment as needed.

### 2. Install the Prometheus Adapter

The Prometheus Adapter bridges Prometheus metrics to the Kubernetes External Metrics API,
which the HPA uses to read IGW signals.

Add the Helm repository and install the adapter into your `monitoring` namespace:
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --create-namespace
```

> **Note:** You must set `prometheus.url` to point to your Prometheus instance. If you are
using `kube-prometheus-stack`, the default service is `http://prometheus-operated.monitoring.svc:9090`.
Pass it at install time or in a values file:
```bash
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.url=http://prometheus-operated.monitoring.svc \
  --set prometheus.port=9090
```

### 3. Configure Prometheus Adapter Rules

Create a values file `igw-adapter-values.yaml` with the following rules:
```yaml
rules:
  external:
    - seriesQuery: 'inference_extension_flow_control_queue_size'
      resources:
        overrides:
          namespace:
            resource: "namespace"
          namespaced: false
      name:
        as: "igw_queue_depth"
      metricsQuery: 'sum(inference_extension_flow_control_queue_size{inference_pool="vllm-llama3-8b-instruct"})'
    - seriesQuery: 'inference_objective_running_requests'
      resources:
        overrides:
          namespace:
            resource: "namespace"
          namespaced: false
      name:
        as: "igw_running_requests"
      metricsQuery: 'sum(inference_objective_running_requests{top_level_controller_name="vllm-llama3-8b-instruct-epp"})'
```

> **Note:** Replace `vllm-llama3-8b-instruct` and `vllm-llama3-8b-instruct-epp` with your
own deployment names.

Apply the rules by upgrading the adapter:
```bash
helm upgrade prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --reuse-values \
  --values igw-adapter-values.yaml
```

Verify the metrics are visible to the Kubernetes API:
```bash
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/default/igw_queue_depth"
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/default/igw_running_requests"
```

A successful response returns a JSON object with the current metric value. A `404` means
the adapter rules are not applied correctly or the Prometheus series does not exist yet —
re-check the `metricsQuery` label values against your live Prometheus data.

### 4. Create the HPA Resource

Below is a sample HPA configuration `hpa.yaml` that uses the dual-metric setup to scale your model server based on both the queue depth and current request load.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: vllm-llama3-8b-instruct-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: vllm-llama3-8b-instruct
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: External
    external:
      metric:
        name: igw_queue_depth
      target:
        type: Value
        value: "250"
  - type: External
    external:
      metric:
        name: igw_running_requests
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

> **Note 1:** The target values (`250`) used here are examples and must be tuned to your model and hardware. A good starting point is to run your model server at a known concurrency level, observe the actual metric values using `kubectl describe hpa`, and set the target below the concurrency at which your model's latency begins to degrade.

> **Note 2:** Although `igw_queue_depth` and `igw_running_requests` originate from the EPP pod, we use `type: External` rather than `type: Pods`. This is because `type: Pods` requires metrics to come from the pods being scaled — in this case the model server pods. Since the EPP is a separate deployment acting as a gateway and emitting metrics on behalf of the model server pool, we treat its metrics as external signals.

### 5. Verify the HPA

Apply the manifest and confirm the HPA is reading metrics:
```bash
kubectl apply -f hpa.yaml
kubectl get hpa vllm-llama3-8b-instruct-hpa -n default
```

A successful deployment would look like this:
```
NAME                          REFERENCE                            TARGETS              MINPODS   MAXPODS   REPLICAS   AGE
vllm-llama3-8b-instruct-hpa   Deployment/vllm-llama3-8b-instruct   0/250, 0/250 (avg)   1         3         1          5m
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
3. **Outcome:** The HPA will de-provision all pods when metrics hit zero and re-provision them as soon as `igw_queue_depth > 0`.

### Option 2: KEDA

If your environment does not allow alpha feature gates, KEDA is a stable alternative.

1. **Setup KEDA:** Install KEDA and follow the [KEDA Prometheus Scaler guide](https://keda.sh/docs/scalers/prometheus/). Note that KEDA comes with its own built-in metrics adapter that is enabled by default when you install KEDA. Unlike HPA, it does not require the Prometheus adapter installation.
2. **Configure Scaler:** Use the same `igw_queue_depth` metric as a trigger.
3. **Outcome:** KEDA scales the deployment from 0 to 1 as soon as a request is queued. Once at 1 pod, the standard HPA (configured with `minReplicas: 1`) takes over to scale up to N.
