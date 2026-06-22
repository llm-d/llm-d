# Autoscaling Workloads with HPA and WVA Metrics

> [!WARNING]
> The VariantAutoscaling CRD has been deprecated in llm-d 0.8.0 in favor of
HPA with the `wva_desired_replicas` external metric. This guide covers the new recommended approach using HPA + WVA Metric. The VariantAutoscaling CRD will be removed in 0.9.0.


The [Workload Variant Autoscaler](https://github.com/llm-d/workload-variant-autoscaler) (WVA) provides dynamic autoscaling capabilities for llm-d inference deployments, automatically adjusting replica counts based on inference server saturation.

## Overview

WVA integrates with llm-d to:

- Dynamically scale inference replicas based on workload saturation
- Optimize resource utilization by adjusting to traffic patterns
- Reduce tail latency through saturation-based scaling decisions

## Prerequisites

Before installing WVA, ensure you have:

1. Enabled monitoring as described in the [autoscaling prerequisites](README.md#prerequisites) section.

1. Installed the [optimized-baseline well-lit path guide](../optimized-baseline/README.md).

> [!NOTE]
> Make sure to deploy the monitoring resources as described in the [optimized-baseline well-lit path guide](../optimized-baseline/README.md#3-optional-enable-monitoring).

## Set Namespaces

```bash
# Namespace where your inference deployment is running
export NAMESPACE=llm-d-optimized-baseline

# Namespace for WVA controller - default to the same namespace as the inference deployment for simplicity, but can be different for cluster-wide autoscaling
export WVA_NAMESPACE=${NAMESPACE}

# Namespace where the monitoring stack (Prometheus) was installed by the prerequisites
export MONITORING_NAMESPACE=llm-d-monitoring

export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

## Installation

> [!NOTE]
> **Namespace-Scoped Installation**: this guide installs WVA in namespace-scoped mode in the `llm-d-optimized-baseline` namespace and configures it to watch resources only in that namespace (`--watch-namespace=llm-d-optimized-baseline`). For cluster-wide autoscaling, set `--watch-namespace=""` in the controller deployment.

1. Choose your platform:

    ```bash
    export PLATFORM=k8s # or ocp
    ```

2.  Extract the Prometheus CA certificate and create a secret for secure communication between WVA and Prometheus:

    ```bash
    # Extract Prometheus CA cert
    PROMETHEUS_CA_CERT=$(kubectl get secret prometheus-web-tls -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.tls\.crt}' | base64 -d)

    # Create generic secret with the CA cert for WVA to access Prometheus API securely
    kubectl create secret generic prometheus-tls-cert \
      --from-literal=ca.crt="${PROMETHEUS_CA_CERT}" \
      --dry-run=client -o yaml | kubectl apply -f - -n ${WVA_NAMESPACE}
    ```

3. Configure Prometheus Adapter Rules (if using Prometheus Adapter as the external metrics provider for WVA):

    ```bash
    helm upgrade prometheus-adapter prometheus-community/prometheus-adapter \
      --namespace ${MONITORING_NAMESPACE} \
      --reuse-values \
      --values ${REPO_ROOT}/guides/workload-autoscaling/components/prometheus-adapter/wva-adapter-values.yaml
    ```

4. Verify the external metrics adapter is registered:

    ```bash
    kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1"

    {"kind":"APIResourceList","apiVersion":"v1","groupVersion":"external.metrics.k8s.io/v1beta1","resources":[{"name":"wva_desired_replicas","singularName":"","namespaced":true,"kind":"ExternalMetricValueList","verbs":["get"]}]}
    ```

5. Install WVA CRDs:

    ```bash
    kubectl apply -k github.com/llm-d/llm-d-workload-variant-autoscaler/config/base/crd?ref=release-0.8
    ```

6. Install WVA controller with Kustomize:

    ```bash
    kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/${PLATFORM} -n ${WVA_NAMESPACE}
    ```

## Verify Installation

Check that the WVA controller is running:

```bash
kubectl get deployment -n ${WVA_NAMESPACE}
NAME                     READY   UP-TO-DATE   AVAILABLE   AGE
wva-controller-manager   2/2     2            2           10m
```

This guide configures the controller deployment with `replicas: 2` and leader election enabled for HA (one active leader plus one standby).

## Enabling Saturation Engine V2 (Recommended)

Saturation engine v2 will be the default in the next release (0.9.0), but for now it must be enabled manually. The v1 saturation engine will be deprecated in 0.9.0 and removed in 0.10.0.

> [!CAUTION]
> Enabling the v2 saturation engine may change the output of the scaling decisions (i.e. `wva_desired_replicas`) for *all* deployments. This may cause a temporary burst of scaling activity.

Edit the WVA configmap to enable the v2 saturation engine:

  ```bash
  kubectl edit configmap wva-saturation-scaling-config -n ${WVA_NAMESPACE}
  ```

  Under the `default:` key, append `analyzers: - name: saturation` to enable the token-based saturation analyzer. The full config should look like this:

  ```yaml
  apiVersion: v1
  data:
    default: |
      # Select the V2 token-based saturation analyzer.
      # Remove this list to fall back to the V1
      # percentage-based analyzer.
      analyzers:
        - name: saturation
      kvCacheThreshold: 0.80
      ...
  ```

The WVA controller will automatically pick up the config change and start using the new saturation engine for scaling decisions. You can verify this by checking the controller logs for messages indicating the active saturation engine. Look for a log line like `V2 saturation analysis completed ` to confirm that the v2 engine is active.


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
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/wva-config/platform/${PLATFORM} -n ${WVA_NAMESPACE}
```

If you installed Prometheus Adapter for WVA, you can uninstall it as well:

```bash
helm uninstall prometheus-adapter -n ${MONITORING_NAMESPACE}
```

## Advanced Configuration, Updates, and Troubleshooting

Please refer to the [Workload Variant Autoscaler documentation](https://github.com/llm-d/llm-d-workload-variant-autoscaler) for advanced configuration options, updating WVA versions, and troubleshooting tips.

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
