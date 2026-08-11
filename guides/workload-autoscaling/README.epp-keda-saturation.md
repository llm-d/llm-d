# [Experimental] Saturation-based Autoscaling

KEDA queries Prometheus directly for two EPP-emitted, InferencePool-scoped signals and scales the model server `Deployment` accordingly. No WVA controller, no Prometheus Adapter — just KEDA, Prometheus, and your model servers.

> [!WARNING]
> This guide is experimental and subject to change. The metrics, configurations, and APIs may evolve as the feature matures. Use in development and test environments only.

This guide keeps the [optimized-baseline](../optimized-baseline/README.md) routing plugins (`approx-prefix-cache-producer`, `inflight-load-producer`, `prefix-cache-affinity-filter`, `token-load-scorer`) and adds the `flowControl` feature gate, which is what makes the EPP export pool saturation metrics.

## Metrics

| Metric | Type | Description | Label |
|---|---|---|---|
| `llm_d_epp_flow_control_pool_saturation` | Gauge | Pool saturation level (0.0 to 1.0+). Values above 1.0 indicate the pool is overloaded and throttling requests. | `inference_pool` |
| `llm_d_epp_request_running` | Gauge | Current number of active in-flight requests across the pool. | `model_name` |

For details on these metrics, see:
- [EPP Flow Control Metrics](../../docs/architecture/core/router/epp/flow-control.md#metrics--observability)
- [EPP Request Handling Metrics](../../docs/architecture/core/router/epp/request-handling.md)

## Prerequisites

Before proceeding, ensure you have:

1. **Monitoring stack with Prometheus over HTTPS** — See [autoscaling prerequisites](README.md#prerequisites) and [Prometheus Setup Guide](../../docs/operations/observability/setup.md). This includes KEDA installation.

2. **EPP flow control enabled** — The `llm_d_epp_flow_control_pool_saturation` metric is only exported when the `flowControl` feature gate is enabled in the EPP's [EndpointPickerConfig](../../docs/api-reference/endpointpickerconfig.md). Configure step 2 below enables it. See [EPP Flow Control](../../docs/architecture/core/router/epp/flow-control.md) for details on flow control behavior.

   > [!IMPORTANT]
   > `EndpointPickerConfig` is the EPP binary's own config schema, **not** a Kubernetes API type — there is no CRD for it. It lives as a key inside the `<release>-epp` ConfigMap, which the EPP mounts at `/config` and reads via `--config-file`. So it is changed by editing that ConfigMap, and cannot be applied as a standalone resource with `kubectl apply` or kustomize.

3. **Optimized-baseline deployment** — Complete the [optimized-baseline guide](../optimized-baseline/README.md).

## Set Namespaces

```bash
# Namespace where your inference deployment is running
export NAMESPACE=llm-d-optimized-baseline

# Namespace where the monitoring stack (Prometheus) was installed
export MONITORING_NAMESPACE=llm-d-monitoring

export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

## Configure

### 1. Create TriggerAuthentication Secret (generic Kubernetes only)

> On **OpenShift**, skip this step — the `ocp` overlay provisions a dedicated
> ServiceAccount and token Secret automatically (see [OpenShift](#openshift) below).

For the bundled kube-prometheus-stack on generic Kubernetes, KEDA needs a bearer token and CA certificate to authenticate with Prometheus. Extract these from the Prometheus ServiceAccount's auto-generated token secret and create a new `prometheus-token` secret in the workload namespace:

```bash
# Get the ServiceAccount's token secret (has a random suffix like prometheus-token-abc123)
SERVICEACCOUNT_SECRET=$(kubectl get serviceaccount prometheus -n ${MONITORING_NAMESPACE} -o jsonpath='{.secrets[0].name}')

# Extract token and CA cert from the ServiceAccount secret
TOKEN=$(kubectl get secret ${SERVICEACCOUNT_SECRET} -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.token}' | base64 -d)
CA_CRT=$(kubectl get secret ${SERVICEACCOUNT_SECRET} -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}' | base64 -d)

# Create prometheus-token secret in the workload namespace with the extracted credentials
kubectl create secret generic prometheus-token \
  --from-literal=token="${TOKEN}" \
  --from-literal=ca.crt="${CA_CRT}" \
  --dry-run=client -o yaml | kubectl apply -f - -n ${NAMESPACE}

# Verify the secret was created
kubectl get secret prometheus-token -n ${NAMESPACE}
```

This creates a secret named `prometheus-token` containing:
- `token`: bearer token for Prometheus authentication
- `ca.crt`: CA certificate for TLS verification

### 2. Enable EPP flow control

The optimized-baseline guide installs the router **without** `flowControl`, so the
saturation metric does not exist yet. The EPP reads its config from the
`optimized-baseline-epp` ConfigMap, so enabling the gate is two commands.

Read the current EPP plugins config:

```bash
kubectl get configmap optimized-baseline-epp -n ${NAMESPACE} \
  -o jsonpath="{.data['optimized-baseline-plugins\.yaml']}"
```

Patch it back with `featureGates` added.

> [!WARNING]
> The patch replaces that key wholesale, so the document you send must be your own
> config plus the gate — not a blind copy of the one below. The document below is the
> plugin list a stock optimized-baseline install produces; if the previous command
> printed anything extra (a `metrics-data-source` plugin, `parameters` blocks, the
> TensorRT-LLM plugin set), carry those lines over or the patch silently drops them.

```bash
kubectl patch configmap optimized-baseline-epp -n ${NAMESPACE} --type merge --patch-file /dev/stdin <<'PATCH'
data:
  optimized-baseline-plugins.yaml: |
    apiVersion: llm-d.ai/v1alpha1
    kind: EndpointPickerConfig
    featureGates:
    - flowControl
    plugins:
    - type: approx-prefix-cache-producer
    - type: inflight-load-producer
    - type: prefix-cache-affinity-filter
    - type: token-load-scorer
    schedulingProfiles:
    - name: default
      plugins:
      - pluginRef: prefix-cache-affinity-filter
      - pluginRef: token-load-scorer
PATCH

kubectl rollout restart deployment/optimized-baseline-epp -n ${NAMESPACE}
```

Notes:
- The EPP reads `--config-file` once at startup, so the restart is required.
- A merge patch rewrites only this one key; the ConfigMap's other keys
  (`default-plugins.yaml`, `payload-agnostic.yaml`, `launch-flags`) are left alone.
- Add `--dry-run=server -o yaml` to the patch to preview the result without applying it.
- `featureGates` must appear only once in the document. If the first command already
  printed a `featureGates` block, flow control is on and you can skip to step 3.
- Helm owns this ConfigMap, so a later `helm upgrade` of the router reverts the patch.
  Re-run these two commands if that happens.

#### Confirm the metric exists

Once the EPP has restarted, it should be exporting saturation:

```bash
kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE} | grep "Flow Control enabled"

kubectl port-forward -n ${NAMESPACE} service/optimized-baseline-epp 9091:9090 &
curl -s http://localhost:9091/metrics | grep llm_d_epp_flow_control_pool_saturation
```

The `inference_pool` label in that output is the value the step 3 query needs:

```
llm_d_epp_flow_control_pool_saturation{inference_pool="optimized-baseline"} 0
```

### 3. Apply the KEDA ScaledObject and TriggerAuthentication

On generic Kubernetes with the bundled kube-prometheus-stack, apply the `k8s` overlay:

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation/k8s -n ${NAMESPACE}
```

On OpenShift, apply the `ocp` overlay instead (see [OpenShift](#openshift) — it handles authentication for you):

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation/ocp -n ${NAMESPACE}
```

Before applying, edit `scaledobject.yaml` to match your deployment:
- Update `inference_pool` in the pool-saturation query to the InferencePool name from step 2 (default: `"optimized-baseline"`)
- Update `model_name` label in the running-requests query (currently: `"Qwen/Qwen3-32B"`)
- Update the `namespace` label in **both** queries to `${NAMESPACE}`. Both are pinned to a namespace on purpose: against a cluster-wide store such as OpenShift's Thanos, an unpinned query aggregates every EPP on the cluster and would scale your deployment on other tenants' traffic
- Update `minReplicaCount`, `maxReplicaCount`, and thresholds for each trigger
- If your Prometheus instance is not the bundled llm-d stack, update `serverAddress` in both triggers

### Platform-specific notes

#### OpenShift

On OpenShift, apply the `ocp` overlay (skip Configure Step 1 — this overlay handles authentication for you):

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation/ocp -n ${NAMESPACE}
```

The overlay:

- Points both triggers at `thanos-querier.openshift-monitoring.svc.cluster.local:9091` and enables `authModes: bearer`. Thanos rejects unauthenticated queries with a 401, and KEDA silently serves `fallback` replicas when a trigger errors, so unauthenticated autoscaling looks healthy while doing nothing.
- Provisions a dedicated `keda-epp-metrics-reader` ServiceAccount granted the `cluster-monitoring-view` ClusterRole, and repoints the `TriggerAuthentication` at that SA's token Secret. On OpenShift the service-ca operator injects `service-ca.crt` (the CA that signs Thanos's serving certificate) into the token Secret automatically, so no `prometheus-token` copy is required.

When deploying this guide to multiple namespaces on a shared cluster, give the `keda-epp-metrics-reader-monitoring-view` ClusterRoleBinding a namespace-unique name so the bindings do not collide.

## Verify

Check that the ScaledObject is ready and KEDA has created its HPA:

```bash
kubectl get scaledobject -n ${NAMESPACE}
kubectl get hpa -n ${NAMESPACE}
```

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

```bash
# Use the overlay you applied: k8s (generic) or ocp (OpenShift).
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation/k8s -n ${NAMESPACE}
# On the generic Kubernetes path only (the ocp overlay provisions no such secret):
kubectl delete secret prometheus-token -n ${NAMESPACE}
```
