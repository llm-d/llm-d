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
| `inference_objective_running_requests` | Requests currently being processed for a model | Maintains a target concurrency per replica |

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

1. Complete the [optimized-baseline guide](../optimized-baseline/README.md) and
   set the guide environment:

   ```bash
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   source ${REPO_ROOT}/guides/env.sh
   export NAMESPACE=llm-d-optimized-baseline
   export MONITORING_NAMESPACE=llm-d-monitoring
   export KEDA_NAMESPACE=keda
   ```

2. Install the bundled kube-prometheus-stack with TLS enabled. The checked-in
   `ScaledObject` and authentication example use this installation:

   ```bash
   ${REPO_ROOT}/guides/recipes/observability/install-prometheus-grafana.sh \
     --enable-tls
   ```

   See the
   [observability setup guide](../../docs/operations/observability/setup.md)
   for additional installation details.

3. [Install KEDA](https://keda.sh/docs/2.20/deploy/) and confirm its operator
   and CRDs are available:

   ```bash
   kubectl get crd scaledobjects.keda.sh
   kubectl get pods -n ${KEDA_NAMESPACE}
   ```

4. Upgrade the optimized-baseline router with the KEDA+EPP values overlay. The
   overlay enables Flow Control and Prometheus monitoring. When monitoring is
   enabled, the router chart exposes the EPP metrics port and creates the
   `ServiceMonitor` that selects it:

   ```bash
   helm upgrade optimized-baseline \
     ${ROUTER_STANDALONE_CHART} \
     -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
     -f ${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml \
     -f ${REPO_ROOT}/guides/workload-autoscaling/keda-epp/router.values.yaml \
     -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
   ```

   Verify Flow Control and monitoring:

   ```bash
   kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE} | \
     grep "Flow Control enabled"
   kubectl get servicemonitor -n ${NAMESPACE}
   ```

### OpenShift environments

The main walkthrough above targets the bundled kube-prometheus-stack and an
upstream KEDA installation in the `keda` namespace. OpenShift installations
that use the Custom Metrics Autoscaler Operator and cluster monitoring access
Prometheus through platform-specific KEDA/CMA and Thanos configuration.

Set `KEDA_NAMESPACE` to the actual CMA/KEDA operator namespace, and replace the
example Prometheus URL and `TriggerAuthentication` with the appropriate Thanos
bearer-token and CA configuration for the cluster. Exact service names,
credentials, and trust configuration are platform-specific and need to be
confirmed with the platform administrator or maintainers; this guide does not
provide validated OpenShift commands.

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
  grep -E 'llm_d_epp_flow_control_queue_size|inference_objective_running_requests'
```

Then stop the EPP port-forward. In terminal 1, open the Prometheus query UI:

```bash
kubectl port-forward -n ${MONITORING_NAMESPACE} \
  service/llmd-kube-prometheus-stack-prometheus 9090:9090
```

Open `https://localhost:9090`, then run:

```promql
sum(llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline", model_name="Qwen/Qwen3-32B"})
```

```promql
sum(inference_objective_running_requests{namespace="llm-d-optimized-baseline", model_name="Qwen/Qwen3-32B"})
```

Each query must return a scalar or a single-element vector. Inspect the raw
series in Prometheus before continuing and update the `namespace`,
`model_name`, or `inference_pool` selectors for your deployment. Scrape-time
labels vary between monitoring installations; do not copy selectors without
checking the live series.

The metrics may remain at zero until requests are sent. If a series is absent,
check the Prometheus target first rather than treating absence as zero.

## Configure Prometheus Authentication

The sample `ScaledObject` uses the TLS-enabled Prometheus service installed by
the llm-d observability setup. Copy its CA into the workload namespace:

```bash
kubectl create secret generic keda-prometheus-auth \
  --namespace ${NAMESPACE} \
  --from-literal=ca.crt="$(kubectl get configmap prometheus-web-tls-ca \
    -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}')" \
  --dry-run=client -o yaml | kubectl apply -f -
```

KEDA reads authentication Secrets from the `ScaledObject` namespace. If your
Prometheus endpoint uses HTTP or requires a bearer token, mTLS, basic
authentication, or cloud workload identity, update each trigger's
`serverAddress` and replace or extend the `TriggerAuthentication` using the
[KEDA Prometheus authentication documentation](https://keda.sh/docs/2.20/scalers/prometheus/#authentication-parameters).
The HTTPS and CA settings in the checked-in example are specific to the
TLS-enabled bundled kube-prometheus-stack. Do not disable TLS verification to
adapt the example.

## Apply the KEDA ScaledObject

Review
[`keda-epp/scaledobject.yaml`](./keda-epp/scaledobject.yaml) before applying it.
At minimum, verify these deployment-specific fields:

- `metadata.namespace`
- `spec.scaleTargetRef.name`
- Prometheus `serverAddress`
- The PromQL label selectors
- Queue-size and running-request thresholds

The checked-in example uses test-friendly bounds and scales the optimized
baseline Deployment between one and three replicas. These limits are not
production defaults. Do not apply them unchanged to a Deployment that should
run more than three replicas; choose bounds appropriate for its capacity and
availability requirements.

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

Both triggers use `AverageValue`, so each threshold is a per-replica target.
For each metric, the generated HPA calculates a desired replica count from the
total metric value and target. Roughly, the aggregate must exceed `threshold ×
current replicas` to request more than the current replica count. When all
configured metrics are available, the HPA uses the largest desired replica
count.

```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/keda-epp
```

The example thresholds are starting points, not universal capacity values.
Measure the concurrency and queue depth at which latency begins to degrade for
your model and hardware, then tune both thresholds.

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

For each `AverageValue` trigger, the generated HPA compares the aggregate value
with its per-replica target and calculates a desired replica count. Scale-up
occurs when that calculation is above the current replica count; it is not a
simple test of whether a total value exceeds the threshold. When both metrics
are available, the HPA selects the largest desired count. Model startup can
take substantially longer than the scaling decision, so distinguish an
increased desired count from a new replica becoming Ready.

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

### Deployment does not scale

Check that no other HPA targets the same Deployment, the HPA calculates a
desired count above the current replica count, `maxReplicaCount` is greater
than the current count, and the generated HPA has no scaling-limited
conditions.

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
