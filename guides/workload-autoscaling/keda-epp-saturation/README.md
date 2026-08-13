# [Experimental] Saturation-based Autoscaling

KEDA queries Prometheus directly for two EPP-emitted, InferencePool-scoped signals and scales the model server `Deployment` accordingly. No WVA controller, no Prometheus Adapter — just KEDA, Prometheus, and your model servers.

> [!WARNING]
> This guide is experimental and subject to change. The metrics, configurations, and APIs may evolve as the feature matures. Use in development and test environments only.

This guide uses the four optimized-baseline plugins provided by llm-d (queue-scorer, kv-cache-utilization-scorer, prefix-cache-scorer, and no-hit-lru-scorer) to enable load-aware and prefix-cache-aware routing alongside pool saturation metrics.

## Metrics

| Metric | Type | Description | Label |
|---|---|---|---|
| `llm_d_epp_flow_control_pool_saturation` | Gauge | Pool saturation level (0.0 to 1.0+). Values above 1.0 indicate the pool is overloaded and throttling requests. | `inference_pool` |
| `llm_d_epp_request_running` | Gauge | Current number of active in-flight requests across the pool. | `model_name` |

For details on these metrics, see:
- [EPP Flow Control Metrics](../../../docs/architecture/core/router/epp/flow-control.md#metrics--observability)
- [EPP Request Handling Metrics](../../../docs/architecture/core/router/epp/request-handling.md)

## Prerequisites

Before proceeding, ensure you have:

1. **Monitoring stack with Prometheus over HTTPS** — See [autoscaling prerequisites](../README.md#prerequisites) and [Prometheus Setup Guide](../../../docs/operations/observability/setup.md). This includes KEDA installation.

2. **EPP flow control enabled** — The `llm_d_epp_flow_control_pool_saturation` metric requires the EPP flow control feature gate to be enabled in your Endpoint Picker configuration. This guide includes an `epp-endpoint-picker-config.yaml` that enables flow control and registers the optimized-baseline plugins. See [EPP Flow Control](../../../docs/architecture/core/router/epp/flow-control.md) for details on flow control behavior.

3. **Optimized-baseline deployment** — Complete the [optimized-baseline guide](../../optimized-baseline/README.md).

## Set Namespaces

Set the guide environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-optimized-baseline
export MONITORING_NAMESPACE=llm-d-monitoring
export MODEL=Qwen/Qwen3-32B
export ENV=existing # options: existing, ocp
export OVERLAY_ROOT=${REPO_ROOT}/guides/workload-autoscaling/keda-epp-saturation/optimized-baseline
```
<!-- guide:env.static end -->

Source the common guide environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

## Configure

### 1. Create TriggerAuthentication Secret (generic Kubernetes only)

> On **OpenShift**, skip this step — the `ocp` overlay provisions a dedicated
> ServiceAccount and token Secret automatically (see [OpenShift](#openshift) below).

For the bundled kube-prometheus-stack on generic Kubernetes, KEDA needs a bearer token and CA certificate to authenticate with Prometheus. Extract these from the Prometheus ServiceAccount's auto-generated token secret and create a new `prometheus-token` secret in the workload namespace:

<!-- guide:deploy.prometheus_auth start -->
```bash
# only when ENV=existing:
SERVICEACCOUNT_SECRET=$(kubectl get serviceaccount prometheus -n ${MONITORING_NAMESPACE} -o jsonpath='{.secrets[0].name}')
TOKEN=$(kubectl get secret ${SERVICEACCOUNT_SECRET} -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.token}' | base64 -d)
CA_CRT=$(kubectl get secret ${SERVICEACCOUNT_SECRET} -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}' | base64 -d)
kubectl create secret generic prometheus-token \
  --from-literal=token="${TOKEN}" \
  --from-literal=ca.crt="${CA_CRT}" \
  --dry-run=client -o yaml | kubectl apply -f - -n ${NAMESPACE}
```
<!-- guide:deploy.prometheus_auth end -->

This creates a secret named `prometheus-token` containing:
- `token`: bearer token for Prometheus authentication
- `ca.crt`: CA certificate for TLS verification

### 2. Apply EPP Config, KEDA ScaledObject, and TriggerAuthentication

On generic Kubernetes with the bundled kube-prometheus-stack, apply the `k8s` overlay:

<!-- guide:deploy.apply_k8s start -->
```bash
# only when ENV=existing:
kubectl apply -k ${OVERLAY_ROOT}/k8s -n ${NAMESPACE}
```
<!-- guide:deploy.apply_k8s end -->

On OpenShift, apply the `ocp` overlay instead (see [OpenShift](#openshift) — it handles authentication for you):

<!-- guide:deploy.apply_ocp start -->
```bash
# only when ENV=ocp:
kubectl apply -k ${OVERLAY_ROOT}/ocp -n ${NAMESPACE}
```
<!-- guide:deploy.apply_ocp end -->

Before applying, edit the manifests to match your deployment:
- `epp-endpoint-picker-config.yaml`: Verify the EPP config is appropriate for your setup. Customize plugin weights if needed.
- `scaledobject.yaml`: 
  - Update `inference_pool` label in the pool-saturation query (currently: `"default"`)
  - Update `model_name` label in the running-requests query (currently: `"Qwen/Qwen3-32B"`)
  - Update `minReplicaCount`, `maxReplicaCount`, and thresholds for each trigger
  - If your Prometheus instance is not the bundled llm-d stack, update `serverAddress` in both triggers

### Platform-specific notes

#### OpenShift

On OpenShift, apply the `ocp` overlay (skip Configure Step 1 — this overlay handles authentication for you):

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/keda-epp-saturation/optimized-baseline/ocp -n ${NAMESPACE}
```

The overlay:

- Points both triggers at `thanos-querier.openshift-monitoring.svc.cluster.local:9091` and enables `authModes: bearer`. Thanos rejects unauthenticated queries with a 401, and KEDA silently serves `fallback` replicas when a trigger errors, so unauthenticated autoscaling looks healthy while doing nothing.
- Provisions a dedicated `keda-epp-metrics-reader` ServiceAccount granted the `cluster-monitoring-view` ClusterRole, and repoints the `TriggerAuthentication` at that SA's token Secret. On OpenShift the service-ca operator injects `service-ca.crt` (the CA that signs Thanos's serving certificate) into the token Secret automatically, so no `prometheus-token` copy is required.

When deploying this guide to multiple namespaces on a shared cluster, give the `keda-epp-metrics-reader-monitoring-view` ClusterRoleBinding a namespace-unique name so the bindings do not collide.

## Verify

Check that the ScaledObject is ready and KEDA has created its HPA:

<!-- guide:verify.tests.scaledobject_hpa start -->
```bash
kubectl get scaledobject -n ${NAMESPACE}
kubectl wait --for=condition=Ready scaledobject --all -n ${NAMESPACE} --timeout=120s
kubectl get hpa -n ${NAMESPACE}
```
<!-- guide:verify.tests.scaledobject_hpa end -->

Expected output:

```
NAME                                    READY   ACTIVE   AGE
optimized-baseline-nvidia-gpu-vllm      True    True     1m

NAME                                    REFERENCE                                      TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
keda-hpa-optimized-baseline-nvidia-gpu  Deployment/optimized-baseline-nvidia-gpu       0%, 0%          1         10        1          1m
```

> [!NOTE]
> KEDA creates its own HPA object from the ScaledObject. Do **not** apply a separate `hpa.yaml` — doing so will cause conflicts.

## Cleanup

<!-- guide:cleanup start -->
```bash
# only when ENV=existing:
kubectl delete -k ${OVERLAY_ROOT}/k8s -n ${NAMESPACE} --ignore-not-found=true

# only when ENV=ocp:
kubectl delete -k ${OVERLAY_ROOT}/ocp -n ${NAMESPACE} --ignore-not-found=true

# only when ENV=existing:
kubectl delete secret prometheus-token -n ${NAMESPACE} --ignore-not-found=true
```
<!-- guide:cleanup end -->
