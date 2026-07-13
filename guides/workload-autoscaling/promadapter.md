# Prometheus Adapter

> [!WARNING]
> The Prometheus Adapter project is planned for deprecation ([kubernetes-sigs/prometheus-adapter#701](https://github.com/kubernetes-sigs/prometheus-adapter/issues/701)). It is recommended to use KEDA for autoscaling.
>
> **For WVA, the Prometheus Adapter is no longer supported.** WVA removed it as a scaler backend in [llm-d-workload-variant-autoscaler#1399](https://github.com/llm-d/llm-d-workload-variant-autoscaler/pull/1399); KEDA is the sole backend. Note that KEDA and the Prometheus Adapter both serve the `v1beta1.external.metrics.k8s.io` APIService and cannot coexist — running both breaks metric delivery for whichever one does not own it. Migrate via [Using KEDA with WVA](./README.wva.md#using-keda-with-wva-recommended). This page remains for the HPA + EPP metrics path.

The Prometheus Adapter bridges Prometheus metrics to the Kubernetes External Metrics API, which the HPA uses to read EPP and WVA signals.

## Prerequisites

You must have a Prometheus instance running in your cluster. See [Prometheus Setup Guide](../../docs/operations/observability/setup.md) for guidance on setting up Prometheus. Make sure to enable TLS as WVA requires it to securely access the Prometheus API.

```bash
export MONITORING_NAMESPACE=llm-d-monitoring
export PROMETHEUS_URL=https://llmd-kube-prometheus-stack-prometheus.$MONITORING_NAMESPACE.svc.cluster.local
export PROMETHEUS_PORT=9090
```

### Platform-specific notes

#### OpenShift

OpenShift User Workload Monitoring operator instal Prometheus in the `openshift-monitoring` namespace.

```bash
export MONITORING_NAMESPACE=openshift-monitoring
export PROMETHEUS_URL=https://thanos-querier.$MONITORING_NAMESPACE.svc.cluster.local
export PROMETHEUS_PORT=9091
```

## Installation

By default Prometheus is installed in the `llm-d-monitoring` namespace and the adapter is configured to connect to it at `https://prometheus-operated.llm-d-monitoring.svc:9090`. If your Prometheus instance is running elsewhere, update the `prometheus.url` value during installation accordingly.


1. Add the Helm repository and install the adapter:

    ```bash
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update

    helm install prometheus-adapter prometheus-community/prometheus-adapter \
    --namespace $MONITORING_NAMESPACE \
    --values components/prometheus-adapter/tls-adapter-values.yaml \
    --set prometheus.url=$PROMETHEUS_URL \
    --set prometheus.port=$PROMETHEUS_PORT

    ```

2. Wait for the adapter to be running and verify it can access Prometheus:

    ```bash
    kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1

    {"kind":"APIResourceList","apiVersion":"v1","groupVersion":"custom.metrics.k8s.io/v1beta1","resources":[{"name":"namespaces/node_filesystem_size_bytes","singularName":"","namespaced":false,"kind":"MetricValueList]}
    ...
    ```
