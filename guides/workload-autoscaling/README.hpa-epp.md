# Autoscaling Workloads with KEDA and EPP Metrics

This guide configures [KEDA](https://keda.sh/) to scale an llm-d model server
Deployment from demand signals emitted by the Endpoint Picker (EPP). It keeps
the existing file name for link compatibility; KEDA is the recommended and
user-facing autoscaling path described here.

## Overview

CPU and GPU utilization are poor scaling signals for LLM inference because an
active accelerator can remain highly utilized at both low and high request
concurrency. EPP exposes signals that describe inference demand directly:

| Metric | Meaning | Scaling role |
|---|---|---|
| `llm_d_epp_flow_control_queue_size` | Requests waiting in EPP Flow Control for backend capacity | Reacts to saturation and sudden bursts |
| `llm_d_epp_request_running` | Active running requests for a model | Maintains a target concurrency per replica |

The scaling path is:

1. EPP exposes metrics on its metrics endpoint.
2. Prometheus scrapes the EPP through a `ServiceMonitor`.
3. KEDA's Prometheus scaler evaluates the configured PromQL queries.
4. KEDA exposes the evaluated values through its metrics server to the
   Kubernetes External Metrics API.
5. KEDA creates and manages a Kubernetes Horizontal Pod Autoscaler (HPA),
   which consumes those external metrics and changes the target Deployment's
   replica count.

Do not create a separate HPA for a Deployment managed by a KEDA
`ScaledObject`. Two HPAs targeting the same Deployment will make conflicting
scaling decisions. The HPA remains visible for inspection, but KEDA owns it.

## Prerequisites

1. Complete the [optimized-baseline guide](../optimized-baseline/README.md),
   including
   [enabling monitoring](../optimized-baseline/README.md#3-optional-enable-monitoring).
   Confirm that Prometheus is scraping the EPP metrics endpoint before
   configuring autoscaling.

   Set the guide environment:

   ```bash
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   source ${REPO_ROOT}/guides/env.sh
   export NAMESPACE=llm-d-optimized-baseline
   export MONITORING_NAMESPACE=llm-d-monitoring
   export KEDA_NAMESPACE=keda
   ```

2. Configure observability by following the shared
   [observability setup guide](../../docs/operations/observability/setup.md).
   Record the Prometheus endpoint and its TLS and authentication requirements;
   you will use them when reviewing the example `ScaledObject`.

3. Install KEDA, or the platform-provided KEDA operator, as described in
   [Kubernetes Metrics Adapter](./README.md#kubernetes-metrics-adapter).

4. Upgrade the optimized-baseline router with the KEDA+EPP overlay. The overlay
   enables EPP Flow Control. Reapply the monitoring feature values used during
   optimized-baseline installation so that the EPP metrics port and its
   `ServiceMonitor` remain enabled:

   ```bash
   helm upgrade optimized-baseline \
     ${ROUTER_STANDALONE_CHART} \
     -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
     -f ${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml \
     -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
     -f ${REPO_ROOT}/guides/workload-autoscaling/keda-epp/router.values.yaml \
     -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
   ```

   Confirm that the pre-existing monitoring configuration remains available
   and that Flow Control is enabled:

   ```bash
   kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE} | \
     grep "Flow Control enabled"
   kubectl get servicemonitor -n ${NAMESPACE}
   ```

## Validate EPP Metrics in Prometheus

First confirm that EPP exposes the metrics directly.

In terminal 1, keep the port-forward running:

```bash
kubectl port-forward -n ${NAMESPACE} \
  service/optimized-baseline-epp 9091:9090
```

In terminal 2, query the endpoint:

```bash
curl -s http://localhost:9091/metrics | \
  grep -E 'llm_d_epp_flow_control_queue_size|llm_d_epp_request_running'
```

Then stop the EPP port-forward. Open the query interface for the Prometheus
installation configured in the observability setup and run:

```promql
sum(llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
```

```promql
sum(llm_d_epp_request_running{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
```

Each query must return a scalar or a single-element vector. Inspect the raw
series in Prometheus before continuing and update the selectors for your
deployment. The running-request metric does not expose `inference_pool`.
Scrape-time labels vary between monitoring installations; do not copy
selectors without checking the live series.

The metrics may remain at zero until requests are sent. If a series is absent,
check the Prometheus target first rather than treating absence as zero.

## Configure Prometheus Authentication

KEDA reads authentication Secrets from the `ScaledObject` namespace. If your
Prometheus endpoint uses HTTP or requires a bearer token, mTLS, basic
authentication, or cloud workload identity, update each trigger's
`serverAddress` and replace or extend the `TriggerAuthentication` using the
[KEDA Prometheus authentication documentation](https://keda.sh/docs/2.20/scalers/prometheus/#authentication-parameters).
The HTTPS and CA settings in the checked-in example are specific to the
TLS-enabled bundled kube-prometheus-stack. Do not disable TLS verification to
adapt the example.

### Platform notes

The Prometheus endpoint, KEDA operator namespace, TLS settings, and
authentication method depend on the platform. Update `KEDA_NAMESPACE`, each
trigger's `serverAddress`, and any `TriggerAuthentication` before applying the
example.

#### Bundled llm-d observability stack

The checked-in `ScaledObject` is written for the TLS-enabled bundled Prometheus
installation documented in the
[observability setup guide](../../docs/operations/observability/setup.md). It
uses the bundled Prometheus service address and a CA copied into the workload
namespace.

To open its Prometheus query UI, keep this command running in a terminal and
open `https://localhost:9090`:

```bash
kubectl port-forward -n ${MONITORING_NAMESPACE} \
  service/llmd-kube-prometheus-stack-prometheus 9090:9090
```

Copy the bundled Prometheus CA into the workload namespace:

```bash
kubectl create secret generic keda-prometheus-auth \
  --namespace ${NAMESPACE} \
  --from-literal=ca.crt="$(kubectl get configmap prometheus-web-tls-ca \
    -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}')" \
  --dry-run=client -o yaml | kubectl apply -f -
```

#### OpenShift

OpenShift environments commonly use the Custom Metrics Autoscaler Operator and
cluster monitoring through Thanos. Use the actual CMA/KEDA namespace and
replace the example Prometheus endpoint and authentication with the
platform-specific Thanos bearer-token and CA configuration. Exact service
names and credentials must be confirmed for the target cluster.

## Configuration

The checked-in `ScaledObject` provides the following default configuration for
this guide:

| Parameter | Value | Tuning guidance |
|---|---|---|
| Target Deployment | `optimized-baseline-nvidia-gpu-vllm-decode` | Replace this with the Deployment to scale. |
| Minimum replicas | 1 | Increase when the deployment requires more warm capacity. |
| Maximum replicas | 8 | Increase or decrease based on accelerator quota, cost, and desired maximum serving capacity. |
| Queue-size threshold | 1 | Decrease to react earlier to queued requests; increase if short queues are acceptable or scaling is too sensitive. |
| Running-request threshold | 16 | Decrease to scale earlier on active concurrency; increase if each replica can safely handle more concurrent requests within latency objectives. |
| Polling interval | 15s | Controls how often KEDA polls triggers while the target is at zero replicas. |
| Cooldown period | 300s | Controls the delay before KEDA scales the target to zero after triggers become inactive. |

## Choosing Scaling Thresholds

The checked-in thresholds provide the default autoscaling configuration for
this guide, but they are not universal capacity values. Validate them for the
model, hardware, and serving configuration used by the target Deployment.

The queue-size trigger reacts to requests waiting in EPP Flow Control because
the current backend capacity cannot accept them. The running-request trigger
reacts to active request concurrency before or alongside sustained queue
growth.

Both triggers use `AverageValue`, so each configured threshold is interpreted
as a per-replica target by the generated HPA. For an aggregated metric, the HPA
calculates a desired replica count from the observed value and that target.
When multiple metrics are configured, the HPA evaluates each metric and uses
the largest desired replica count.

Validate the thresholds with representative load tests. Observe queue growth,
running-request concurrency, latency objectives, model cold-start time, and the
point at which additional replicas become useful. Set `maxReplicaCount` high
enough to provide the required capacity while respecting accelerator quotas
and cluster limits.

Do not assume that values validated for one model, accelerator type, tensor
parallel configuration, or request distribution apply to another deployment.
Future benchmarking can provide more specific recommendations for validated
model and hardware combinations.

## Apply the KEDA ScaledObject

Review
[`keda-epp/scaledobject.yaml`](./keda-epp/scaledobject.yaml) before applying it.
At minimum, verify these deployment-specific fields:

- `metadata.namespace`
- `spec.scaleTargetRef.name`
- Prometheus `serverAddress`
- The PromQL label selectors
- Queue-size and running-request thresholds

This walkthrough intentionally begins with one target replica so that a 1-to-N
scale-up is observable. Scale the target Deployment down before creating the
`ScaledObject`, then wait for it to become available:

```bash
kubectl scale deployment optimized-baseline-nvidia-gpu-vllm-decode \
  -n ${NAMESPACE} --replicas=1
kubectl rollout status \
  deployment/optimized-baseline-nvidia-gpu-vllm-decode \
  -n ${NAMESPACE} --timeout=15m
```

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/keda-epp
```

## Verify KEDA Metric Evaluation

Check the `ScaledObject` status and events:

```bash
kubectl get scaledobject optimized-baseline-keda-epp -n ${NAMESPACE}
kubectl describe scaledobject optimized-baseline-keda-epp -n ${NAMESPACE}
```

`Ready=True` confirms that the scaler configuration is valid. Because this
example has `minReplicaCount: 1`, the `Active` condition is not the best signal
for 1-to-N scaling. Inspect the generated HPA's current metrics and the target
Deployment's replica count instead. `Active` becomes relevant to zero-to-one
activation in the optional scale-to-zero configuration below.

KEDA creates the HPA named in `horizontalPodAutoscalerConfig`:

```bash
kubectl get hpa keda-hpa-optimized-baseline -n ${NAMESPACE}
kubectl get hpa keda-hpa-optimized-baseline -n ${NAMESPACE} \
  -o jsonpath='{.status.currentMetrics}' | jq
```

A non-empty `currentMetrics` list shows that the generated HPA is receiving
the metrics exposed by KEDA. It can take several polling intervals for the
first values to appear.

## Generate Bounded Load

Run a temporary curl pod in the workload namespace:

```bash
kubectl run curl-load --rm -it \
  --image=curlimages/curl \
  --restart=Never \
  --namespace=${NAMESPACE} -- sh
```

From inside the pod, send a bounded set of concurrent requests:

```bash
cat > /tmp/request.json <<'EOF'
{
  "model": "Qwen/Qwen3-32B",
  "prompt": "Write a detailed explanation of how continuous batching works.",
  "max_tokens": 256
}
EOF

seq 1 100 | xargs -P 16 -I{} \
  curl -sS --max-time 180 -o /dev/null -w '%{http_code}\n' \
    -X POST http://optimized-baseline-epp/v1/completions \
    -H 'Content-Type: application/json' \
    --data-binary @/tmp/request.json
```

Adjust concurrency only if the reference load does not cross either configured
threshold. Keep request counts and timeouts bounded while tuning.

## Verify Scale-Up

While the load is running, watch the ScaledObject, generated HPA, and target
Deployment:

```bash
kubectl get scaledobject,hpa -n ${NAMESPACE} -w
```

```bash
kubectl get deployment optimized-baseline-nvidia-gpu-vllm-decode \
  -n ${NAMESPACE} -w
```

An increased desired replica count confirms that the HPA made a scale-up
decision. A new replica can take substantially longer to become Ready while the
model is loading.

After the additional replica is Ready, repeat a normal inference request and
confirm it succeeds.

## Troubleshooting

### ScaledObject is not Ready

```bash
kubectl describe scaledobject optimized-baseline-keda-epp -n ${NAMESPACE}
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp'
kubectl logs -n ${KEDA_NAMESPACE} \
  -l app.kubernetes.io/name=keda-operator --all-containers
```

Common causes are an unreachable `serverAddress`, an untrusted Prometheus CA,
missing authentication, or a PromQL query that returns more than one element.

### Generated HPA shows unknown metrics

Re-run the exact query in Prometheus, verify its labels, and inspect the
generated HPA:

```bash
kubectl describe hpa keda-hpa-optimized-baseline -n ${NAMESPACE}
```

Do not create a second HPA to work around this condition. Fix the ScaledObject
query or Prometheus connectivity instead.

### Metrics are missing

```bash
kubectl get servicemonitor -n ${NAMESPACE} -o yaml
kubectl get endpoints optimized-baseline-epp -n ${NAMESPACE}
kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE}
```

Confirm the Prometheus target is `UP`, Flow Control is enabled, and the live
metric labels match the selectors in the ScaledObject.

By default, the KEDA Prometheus scaler ignores an empty Prometheus result
(`ignoreNullValues` defaults to `true`). If a scaler remains inactive
unexpectedly, verify that the PromQL query returns a value rather than relying
only on status conditions.

### Desired replicas increase but new replicas are not Ready

If the generated HPA raises the desired replica count but the Deployment's
Ready replica count does not increase, the scaler has already made its
decision. Inspect pod events, scheduling status, image or model download
progress, and model-server logs. Model startup delay is distinct from a
Prometheus or HPA metric failure.

### Deployment does not scale

Check whether another HPA or controller targets the same Deployment. This can
happen when a manually created HPA remains alongside KEDA or another
autoscaling controller manages the workload.

If autoscaling is managed exclusively by KEDA and there is exactly one
`ScaledObject` for the target Deployment, KEDA owns the generated HPA and this
duplicate-HPA scenario should not occur.

Also check that the HPA calculates a desired count above the current replica
count, `maxReplicaCount` is greater than the current count, metrics are
available, and the generated HPA has no scaling-limited conditions.

## Cleanup

```bash
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/keda-epp
kubectl delete secret keda-prometheus-auth -n ${NAMESPACE}
```

Deleting the `ScaledObject` also removes the HPA managed by KEDA. It does not
delete the target Deployment and can leave that Deployment at its current
replica count. Scale the Deployment explicitly if a different post-cleanup
count is required.

## Optional: Scale to Zero

KEDA supports scale-to-zero without the Kubernetes `HPAScaleToZero` feature
gate. Set `minReplicaCount: 0` only after validating scale-up from one replica.
When the Deployment is at zero, the Flow Control queue-size metric is the
activation signal: EPP holds incoming requests until a model server becomes
Ready.

At zero replicas, the `Active` condition indicates whether at least one trigger
has crossed its activation threshold. `cooldownPeriod` controls how long KEDA
waits before scaling from one replica to zero. While one or more replicas are
running, ordinary scale-down is controlled by the generated HPA's behavior,
including its stabilization window and policies.

Scale-to-zero introduces model cold-start latency. EPP queues are in memory, so
queued requests are lost if EPP restarts, and clients must allow enough time
for the model to load. Treat these as production availability considerations,
not only autoscaler settings.

## Legacy Prometheus Adapter Path

Existing direct-HPA deployments can refer to the
[Prometheus Adapter notes](./promadapter.md) while migrating. New EPP
autoscaling deployments should use KEDA and should not install Prometheus
Adapter solely for this guide.
