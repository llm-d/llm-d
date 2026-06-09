# Autoscaling with Workload Variant Autoscaler (WVA)

[![Nightly - WVA E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml) [![Nightly - WVA E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml)

The [Workload Variant Autoscaler](https://github.com/llm-d-incubation/workload-variant-autoscaler) (WVA) provides dynamic autoscaling capabilities for llm-d inference deployments, automatically adjusting replica counts based on inference server saturation.

## Overview

WVA integrates with llm-d to:

- Dynamically scale inference replicas based on workload saturation
- Optimize resource utilization by adjusting to traffic patterns
- Reduce tail latency through saturation-based scaling decisions

## Prerequisites

Before installing WVA, ensure you have:

1. Installed the llm-d inference stack from one of the well-lit path guides.

    > **Note**: WVA requires HTTPS connections to Prometheus for metric collection. When installing the [monitoring stack](../../docs/resources/observability/setup.md), ensure to enable HTTPS/TLS support.

    > **Note**: If selecting namespace-scoped mode below, make sure to install the optimized-baseline stack in the same namespace as WVA (by default `llm-d-autoscaler`).

    > **Note**: Currently, WVA does not support the Wide Expert Parallelism (EP/DP) with LeaderWorkerSet well-lit path. Support for this will be added in a future release.

2. An external metrics provider installed and configured in your cluster (e.g., Prometheus together with Prometheus Adapter or KEDA). WVA relies on external metrics to make scaling decisions. See [Install Prometheus Adapter (Required Dependency)](#install-prometheus-adapter-required-dependency) for installation instructions.

3. Prometheus Operator CRDs are installed before applying WVA overlays (required for `ServiceMonitor` resources):

```bash
kubectl api-resources | rg ServiceMonitor
```

## Set Namespaces

```bash
export WVA_NAMESPACE=llm-d-autoscaler
export NAMESPACE=llm-d-optimized-baseline
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace ${WVA_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

> **Default mode**: this guide installs WVA scoped to `llm-d-optimized-baseline` by default.

**For OpenShift only**, ensure both namespaces have the monitoring label:

```bash
kubectl label namespace "${NAMESPACE}" openshift.io/user-monitoring=true --overwrite
kubectl label namespace "${WVA_NAMESPACE}" openshift.io/user-monitoring=true --overwrite
```

## Platform-Specific Configuration

### OpenShift

> [!NOTE]
> OpenShift User Workload Monitoring must be enabled for the namespaces used by this guide.

Configure WVA to query the cluster Thanos Querier:

```bash
cat ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/ocp/configmap-patch.yaml
```

OpenShift defaults are already set in the overlay:

- `PROMETHEUS_BASE_URL=https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`
- `PROMETHEUS_TLS_INSECURE_SKIP_VERIFY=true`

Optional (strict TLS): manage the `prometheus-client-cert` secret with Kustomize.

```bash
export PROMETHEUS_CA_CERT=$(kubectl get secret thanos-querier-tls -n openshift-monitoring -o jsonpath='{.data.tls\.crt}' | base64 -d)
WVA_TLS_OVERLAY_DIR=$(mktemp -d)
cp -R ${REPO_ROOT}/guides/workload-autoscaling/components/tls-overlay/. "${WVA_TLS_OVERLAY_DIR}"
printf "%s" "${PROMETHEUS_CA_CERT}" > "${WVA_TLS_OVERLAY_DIR}/ca.crt"
kubectl apply -k "${WVA_TLS_OVERLAY_DIR}" -n ${WVA_NAMESPACE}
```

### GKE

GMP doesn't expose HTTP API. Deploy in-cluster Prometheus:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n llm-d-monitoring --create-namespace
```

Then configure WVA to query the in-cluster Prometheus:

```bash
export MON_NS=${MON_NS:-llm-d-monitoring}
export PROMETHEUS_BASE_URL=https://kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local:9090
export PROMETHEUS_TLS_INSECURE_SKIP_VERIFY=true
```

For production, use strict TLS verification:

```bash
export PROMETHEUS_TLS_INSECURE_SKIP_VERIFY=false
# Ensure the controller trusts the Prometheus CA via secret `prometheus-client-cert` (key: ca.crt)
```

## Installation

Optional preflight: validate platform overlays render before applying:

```bash
kubectl kustomize ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/ocp >/dev/null
kubectl kustomize ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/generic >/dev/null
kubectl kustomize ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/gke >/dev/null
```

Install WVA using the OpenShift overlay in `wva-config/platform/ocp`:

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/ocp -n ${WVA_NAMESPACE}
```

If you are not on OpenShift, use:

```bash
# Generic Kubernetes
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/generic -n ${WVA_NAMESPACE}

# GKE
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/gke -n ${WVA_NAMESPACE}
```

> **Note**: By default, this install watches `llm-d-optimized-baseline` (`--watch-namespace=llm-d-optimized-baseline`).

## Verify Installation

Check that the WVA controller is running:

```bash
kubectl get deployment -n ${WVA_NAMESPACE}
NAME                                                       READY   UP-TO-DATE   AVAILABLE   AGE
workload-variant-autoscaler-controller-manager              2/2     2            2           10m
```

This guide configures the controller deployment with `replicas: 2` and leader election enabled for HA (one active leader plus one standby).

## Enabling Autoscaling for an Inference Deployment

This section enables autoscaling for an existing [optimized-baseline](../optimized-baseline/README.md) deployment. It creates a `VariantAutoscaling` CR and an HPA that reads the `wva_desired_replicas` metric.

> **Important:** while installing optimized-baseline, make sure monitoring is enabled so vLLM metrics are scraped before applying this autoscaling overlay:
> `kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring`

### Apply the Kustomize Overlay

```bash
kubectl apply -k optimized-baseline-autoscaling -n ${NAMESPACE}
```

> **Note:** `${NAMESPACE}` should match the namespace where the optimized-baseline stack is running (commonly `llm-d-optimized-baseline`).

> **Note:** If you set the `RELEASE_NAME_POSTFIX` environment variable when installing the optimized-baseline stack, you need to set the same postfix in the `kustomization.yaml` of this overlay to ensure the correct resources are targeted. For example, if you set `RELEASE_NAME_POSTFIX=my-custom` during installation, you should uncomment the line `nameSuffix: -my-custom` in the `kustomization.yaml` of this overlay.

### Verify

After a few minutes, you should see the new `VariantAutoscaling` resource:

```bash
kubectl get variantautoscaling optimized-baseline-nvidia-gpu-vllm-decode -n ${NAMESPACE}
```

Expected output:

```
NAME                                        TARGET                                      MODEL            OPTIMIZED   METRICSREADY   AGE
optimized-baseline-nvidia-gpu-vllm-decode   optimized-baseline-nvidia-gpu-vllm-decode   Qwen/Qwen3-32B   1           True           37m
```

You should also see the HPA with the `wva_desired_replicas` metric:

```bash
kubectl get hpa -n ${NAMESPACE}
NAME                                        REFERENCE                                              TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
optimized-baseline-nvidia-gpu-vllm-decode   Deployment/optimized-baseline-nvidia-gpu-vllm-decode   0%/1         1         16         1         37m
```

### Cleanup

To remove the autoscaling configuration, delete the Kustomize overlay:

```bash
kubectl delete -k optimized-baseline-autoscaling/ -n ${NAMESPACE}
```

## WVA Controller Cleanup

Remove the WVA controller with Kustomize:

```bash
# OpenShift
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/ocp -n ${WVA_NAMESPACE}

# Generic Kubernetes
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/generic -n ${WVA_NAMESPACE}

# GKE
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/gke -n ${WVA_NAMESPACE}
```

If you installed Prometheus Adapter for WVA, you can uninstall it as well:

```bash
helm uninstall prometheus-adapter -n ${MON_NS:-llm-d-monitoring}
```

## Advanced Configuration, Updates, and Troubleshooting

Please refer to the [Workload Variant Autoscaler documentation](https://github.com/llm-d-incubation/workload-variant-autoscaler) for advanced configuration options, updating WVA versions, and troubleshooting tips.

## Install Prometheus Adapter (Required Dependency)

Choose your platform and follow the corresponding section:

### On OpenShift

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

### On GKE/Generic Kubernetes

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export VERSION=${VERSION:-v0.7.0}
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/${VERSION}/config/samples/prometheus-adapter-values.yaml

# Update Prometheus URL
sed -i.bak "s|url:.*|url: https://kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local:9090|" ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml || \
  echo "Edit ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml to set prometheus.url"

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml
```

### On Kind/HTTPS Prometheus

For Kind clusters with HTTPS Prometheus (configured in Platform-Specific Configuration), the `prometheus-ca` ConfigMap is created by WVA during controller installation. Configure Prometheus Adapter to use it:

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export VERSION=${VERSION:-v0.7.0}
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/${VERSION}/config/samples/prometheus-adapter-values.yaml

# Configure values with CA cert (ConfigMap created by WVA during controller installation)
cat >> ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml <<EOF
prometheus:
  url: https://kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local
  port: 9090
extraArguments:
  - --prometheus-ca-file=/etc/ssl/certs/prometheus-ca.crt
extraVolumeMounts:
  - name: prometheus-ca
    mountPath: /etc/ssl/certs/prometheus-ca.crt
    subPath: ca.crt
    readOnly: true
extraVolumes:
  - name: prometheus-ca
    configMap:
      name: prometheus-ca
EOF

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml
```

> **Note**: WVA creates the `prometheus-ca` ConfigMap in the monitoring namespace using the configured CA cert settings. This ConfigMap is required for Prometheus Adapter.

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
