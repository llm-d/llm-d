# Autoscaling with Workload Variant Autoscaler (WVA)

The [Workload Variant Autoscaler](https://github.com/llm-d-incubation/workload-variant-autoscaler) (WVA) provides dynamic autoscaling capabilities for llm-d inference deployments, automatically adjusting replica counts based on inference server saturation.

## Overview

WVA integrates with llm-d to:

- Dynamically scale inference replicas based on workload saturation
- Optimize resource utilization by adjusting to traffic patterns
- Reduce tail latency through saturation-based scaling decisions

## Prerequisites

Before installing WVA, ensure you have:

1. Installed the [optimized-baseline well-lit path guide](../optimized-baseline/README.md).

    > [!NOTE]
    > WVA requires HTTPS connections to Prometheus for metric collection. When installing the [monitoring stack](../../docs/resources/observability/setup.md), ensure to enable HTTPS/TLS support.

    > [!NOTE]
    > Make sure to enable monitoring as described in the [optimized-baseline well-lit path guide](../optimized-baseline/README.md#3-optional-enable-monitoring).

2. An external metrics provider installed and configured in your cluster (e.g., Prometheus together with Prometheus Adapter or KEDA). HPA relies on the external metric exposed by WVA, `wva_desired_replicas`, to make scaling decisions. See [Install Prometheus Adapter (Required Dependency)](#install-prometheus-adapter-required-dependency) for installation instructions.

    > [!NOTE]
    > This guide relies on prometheus adapter to expose WVA's desired replica count as an external metric for HPA. KEDA is the recommended alternative and this guide will be updated to include KEDA instructions in a future release.

3. [OpenShift User Workload Monitoring](https://docs.redhat.com/en/documentation/openshift_container_platform/4.14/html/monitoring/configuring-user-workload-monitoring) enabled for the namespaces used by this guide.


## Set Namespaces

```bash
export NAMESPACE=llm-d-optimized-baseline
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace ${WVA_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

> [!NOTE]
> **Namespaced-Scoped Installation**: this guide installs WVA to watch resources only in the `llm-d-optimized-baseline` namespace. For cluster-wide autoscaling, set `--watch-namespace=""` in the controller deployment.


## Installation

> [!NOTE]
>  By default, WVA is configured to watch `llm-d-optimized-baseline` (`--watch-namespace=llm-d-optimized-baseline`) only. To enable cluster-wide autoscaling, set `--watch-namespace=""` in the controller deployment and ensure all target HPA/KEDA objects are annotated with `llm-d.ai/managed: "true"`.

- Create a secret with the Prometheus CA certificate for secure communication between WVA and Prometheus:

  ```bash
  PROMETHEUS_CA_CERT=$(kubectl get secret thanos-querier-tls -n openshift-monitoring -o jsonpath='{.data.tls\.crt}' | base64 -d)
  kubectl create secret generic prometheus-client-cert \
    --from-literal=ca.crt="${PROMETHEUS_CA_CERT}" \
    --dry-run=client -o yaml | kubectl apply -f - -n ${WVA_NAMESPACE}
  ```

- Install WVA:

  ```bash
  kubectl apply -k guides/workload-autoscaling/wva-config/platform/ocp -n ${WVA_NAMESPACE}
  ```

## Verify Installation

Check that the WVA controller is running:

```bash
kubectl get deployment -n ${WVA_NAMESPACE}
NAME                     READY   UP-TO-DATE   AVAILABLE   AGE
wva-controller-manager   2/2     2            2           10m
```

This guide configures the controller deployment with `replicas: 2` and leader election enabled for HA (one active leader plus one standby).

## Enabling Autoscaling for an Inference Deployment

This section enables autoscaling for an existing [optimized-baseline](../optimized-baseline/README.md) deployment. It creates an HPA with WVA discovery annotations that reads the `wva_desired_replicas` metric. WVA discovers the deployment via the `llm-d.ai/managed: "true"` annotation and publishes the desired replica count as an external metric consumed by HPA/KEDA.


### Apply the Kustomize Overlay

```bash
kubectl apply -k optimized-baseline-autoscaling -n ${NAMESPACE}
```

> [!NOTE]
> `${NAMESPACE}` should match the namespace where the optimized-baseline stack is running (commonly `llm-d-optimized-baseline`).

### Verify

After a few minutes, you should see the HPA with the `wva_desired_replicas` metric:

```bash
kubectl get hpa -n ${NAMESPACE}
```

Expected output:

```
NAME                                        REFERENCE                                              TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
optimized-baseline-nvidia-gpu-vllm-decode   Deployment/optimized-baseline-nvidia-gpu-vllm-decode   0%/1         1         16         1         37m
```

Confirm WVA is managing the HPA by checking its annotations:

```bash
kubectl get hpa optimized-baseline-nvidia-gpu-vllm-decode -n ${NAMESPACE} -o jsonpath='{.metadata.annotations}' | jq .
```

Expected output includes `"llm-d.ai/managed": "true"`, `"llm-d.ai/model-id"`, and `"llm-d.ai/variant-cost"`.

### Cleanup

To remove the autoscaling configuration, delete the Kustomize overlay:

```bash
kubectl delete -k optimized-baseline-autoscaling/ -n ${NAMESPACE}
```

## WVA Controller Cleanup

Remove the WVA controller with Kustomize:

```bash
kubectl delete -k guides/workload-autoscaling/wva-config/platform/ocp -n ${WVA_NAMESPACE}
```

If you installed Prometheus Adapter for WVA, you can uninstall it as well:

```bash
helm uninstall prometheus-adapter -n ${MON_NS:-llm-d-monitoring}
```

## Advanced Configuration, Updates, and Troubleshooting

Please refer to the [Workload Variant Autoscaler documentation](https://github.com/llm-d-incubation/workload-variant-autoscaler) for advanced configuration options, updating WVA versions, and troubleshooting tips.

## Install Prometheus Adapter (Required Dependency)

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export VERSION=${VERSION:-v0.7.0}
export MON_NS=openshift-user-workload-monitoring

# Download OpenShift-specific values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/${VERSION}/config/samples/prometheus-adapter-values-ocp.yaml

# Update Prometheus URL
sed -i.bak "s|url:.*|url: https://thanos-querier.openshift-monitoring.svc.cluster.local|" ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml || \
  echo "Edit ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml to set prometheus.url"

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace \
  -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  -f ${REPO_ROOT}/guides/workload-autoscaling/components/prometheus-adapter/values-wva-external-metric.yaml

# Verify that WVA metric is discoverable by external metrics API
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1" | jq .

# Verify RBAC permissions
kubectl auth can-i --list --as=system:serviceaccount:${MON_NS}:prometheus-adapter | grep -E "monitoring.coreos.com|prometheuses|namespaces"

# Create ClusterRole for Prometheus API access if needed
kubectl apply -f - <<'YAML'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: allow-thanos-querier-api-access
rules:
- nonResourceURLs: [/api/v1/query, /api/v1/query_range, /api/v1/labels, /api/v1/label/*/values, /api/v1/series, /api/v1/metadata, /api/v1/rules, /api/v1/alerts]
  verbs: [get]
- apiGroups: [monitoring.coreos.com]
  resourceNames: [k8s]
  resources: [prometheuses/api]
  verbs: [get, create, update]
- apiGroups: [""]
  resources: [namespaces]
  verbs: [get]
YAML
```

**Verify installation**: `kubectl get pods -n ${MON_NS} -l app.kubernetes.io/name=prometheus-adapter`


## Benchmark Results

These benchmarks measure **single-variant autoscaling** — one model variant scaled dynamically by WVA based on saturation. Benchmarks were run on an NVIDIA H100 cluster (OpenShift) with a Poisson arrival profile. All values are **3-run averages**.

### Configuration

WVA v1 Saturation (Default) drives scaling decisions; HPA acts on the `wva_desired_replicas` metric WVA produces.

| Component | Parameter | Value |
|---|---|---|
| WVA | KV cache threshold | 0.80 |
| WVA | Queue length threshold | 5 |
| WVA | KV spare trigger | 0.10 |
| WVA | Queue spare trigger | 3 |
| WVA | Enable limiter | false |
| HPA | Min / Max replicas | 1 / 10 |
| HPA | Scale-up stabilization | 0 s |
| HPA | Scale-up policy | 10 Pods / 150 s |
| HPA | Scale-down stabilization | 240 s |
| HPA | Scale-down policy | 10 Pods / 150 s |
| HPA | Metric source | External (`wva_desired_replicas`) |

### Results

#### Prefill Heavy
> 4,000 input tokens · 1,000 output tokens · 20 RPS

| Model | Duration | Load Gen | P99 TTFT (ms) | P99 ITL (ms/tok) | Avg Replicas | Max Replicas | Avg KV Cache | Avg Queue Depth | Errors | Pod Startup (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-32B | 600 s | GuideLLM | 98,420 | 54.8 | 1.73 | 3 | 66.3% | 236.5 | 4,184 | 110 |
| Qwen3-0.6B | 600 s | GuideLLM | 81,391 | 51.9 | 1.93 | 3 | 65.1% | 76.5 | 401 | 65 |
| Qwen3-0.6B | 1800 s | GuideLLM | 66,177 | 47.3 | 3.17 | 5 | 55.7% | 41.2 | 860 | 66 |

#### Decode Heavy
> 1,000 input tokens · 4,000 output tokens · 20 RPS

| Model | Duration | Load Gen | P99 TTFT (ms) | P99 ITL (ms/tok) | Avg Replicas | Max Replicas | Avg KV Cache | Avg Queue Depth | Errors | Pod Startup (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-32B | 600 s | GuideLLM | 78,051 | 47.1 | 1.84 | 3 | 79.2% | 108.8 | 3,563 | 109 |
| Qwen3-0.6B | 600 s | GuideLLM | 62,296 | 41.1 | 1.89 | 3 | 61.7% | 51.1 | 1,408 | 65 |
| Qwen3-0.6B | 1800 s | GuideLLM | 58,934 | 44.8 | 2.59 | 4 | 57.2% | 30.8 | 2,520 | 66 |

#### Bursty
> ~1,000 input tokens · ~1,000 output tokens · Multi-stage RPS (15 → 2 → 10 → 15 → 5 → 2)

| Model | Duration | Load Gen | P99 TTFT (ms) | P99 ITL (ms/tok) | Avg Replicas | Max Replicas | Avg KV Cache | Avg Queue Depth | Errors | Pod Startup (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-32B | 900 s | GuideLLM | 262,441 | 196.3 | 2.43 | 4 | 45.1% | 53.5 | 6,110 | 103 |
| Qwen3-0.6B | 900 s | inference-perf | 13,376 | 48.0 | 1.99 | 3 | 35.2% | 16.0 | 51 | 66 |
| Qwen3-0.6B | 1800 s | inference-perf | 23,278 | 50.1 | 1.63 | 3 | 29.5% | 1.1 | 71 | 64 |

#### Symmetrical
> 1,000 input tokens · 1,000 output tokens · 20 RPS

| Model | Duration | Load Gen | P99 TTFT (ms) | P99 ITL (ms/tok) | Avg Replicas | Max Replicas | Avg KV Cache | Avg Queue Depth | Errors | Pod Startup (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-32B | 600 s | GuideLLM | 100,187 | 67.3 | 1.70 | 3 | 70.2% | 166.8 | 3,729 | 103 |
| Qwen3-0.6B | 600 s | GuideLLM | 23,169 | 43.3 | 1.80 | 3 | 52.0% | 13.0 | 17 | 64 |
| Qwen3-0.6B | 1800 s | GuideLLM | 20,825 | 40.4 | 1.80 | 3 | 46.8% | 10.8 | 342 | 66 |

### Metric Definitions

| Metric | Definition |
|---|---|
| P99 TTFT | 99th-percentile time-to-first-token (ms) — lower is better |
| P99 ITL | 99th-percentile inter-token latency (ms/token) — lower is better |
| Avg Replicas | Mean pod count during the test window |
| Avg KV Cache | Mean GPU KV cache utilization |
| Avg Queue Depth | Mean pending-request queue depth at the endpoint proxy (EPP) |
| Pod Startup | Average time for a new replica to become ready (s) |

> Full per-run data and additional WVA tuning variants are in the [upstream benchmark doc](https://github.com/llm-d/llm-d-workload-variant-autoscaler/blob/main/docs/benchmark.md).

## FAQ

**Q: How do I know which external metrics provider (Prometheus Adapter vs KEDA) is used?**

A: run this command and check the output:

```bash
kubectl get apiservice v1beta1.external.metrics.k8s.io -o yaml
```
