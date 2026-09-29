# Autoscaling Workloads with KEDA and EPP Metrics

This guide configures [KEDA](https://keda.sh/) to scale an llm-d model server
Deployment from demand signals emitted by the Endpoint Picker (EPP). KEDA is the
recommended and user-facing autoscaling path described here.

## Overview

CPU and GPU utilization are poor scaling signals for LLM inference because an
active accelerator can remain highly utilized at both low and high request
concurrency. EPP exposes signals that describe inference demand directly:

| Metric | Meaning | Scaling role |
|---|---|---|
| `llm_d_epp_flow_control_queue_size` | Requests waiting in EPP Flow Control for backend capacity | Queue signal (default) - reacts to saturation and sudden bursts |
| `llm_d_epp_flow_control_pool_saturation` | Pool saturation level (0.0-1.0+) | Saturation signal (alternative) - reacts to pool utilization before requests queue |
| `llm_d_epp_request_running` | Active running requests for a model | Shared per-replica concurrency target on both paths |

This guide ships two scaling signals as selectable overlays; queue depth is the
default. Both originate in the EPP flow-control subsystem, so both require the
flow-control feature gate enabled by this guide's `router.values.yaml`. See
[Choosing a Scaling Signal](#choosing-a-scaling-signal) below.

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

1. Complete the [optimized-baseline guide](../../optimized-baseline/README.md),
   including
   [enabling monitoring](../../optimized-baseline/README.md#3-optional-enable-monitoring).
   Confirm that Prometheus is scraping the EPP metrics endpoint before
   configuring autoscaling.

Set the guide environment variables. `TRIGGER` selects the scaling signal
(`queue` or `saturation`) and `ENV` selects the platform (`existing` or `ocp`);
together they name the overlay you apply:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-optimized-baseline
export MONITORING_NAMESPACE=llm-d-monitoring
export KEDA_NAMESPACE=keda
export MODEL=Qwen/Qwen3-32B
export TARGET_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
export SCALEDOBJECT_NAME=optimized-baseline-keda-epp
export HPA_NAME=keda-hpa-optimized-baseline
export TRIGGER=queue # options: queue, saturation
export ENV=existing # options: existing, ocp
export OVERLAY_ROOT=${REPO_ROOT}/guides/workload-autoscaling/keda-epp/optimized-baseline
```
<!-- guide:env.static end -->

Source the common guide environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

1. Configure observability by following the shared
   [observability setup guide](../../../docs/operations/observability/setup.md).
   Record the Prometheus endpoint and its TLS and authentication requirements;
   you will use them when reviewing the example `ScaledObject`.

2. Install KEDA, or the platform-provided KEDA operator, as described in
   [Kubernetes Metrics Adapter](../README.md#kubernetes-metrics-adapter).

3. Upgrade the optimized-baseline router with the KEDA+EPP overlay. The overlay
   enables EPP Flow Control. Reapply the monitoring feature values used during
   optimized-baseline installation so that the EPP metrics port and its
   `ServiceMonitor` remain enabled:

<!-- guide:prerequisites.router start -->
```bash
helm upgrade optimized-baseline \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -f ${OVERLAY_ROOT}/router.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:prerequisites.router end -->

   Confirm that the pre-existing monitoring configuration remains available
   and that Flow Control is enabled:

<!-- guide:prerequisites.confirm start -->
```bash
kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE} | grep "Flow Control enabled"
kubectl get servicemonitor -n ${NAMESPACE}
```
<!-- guide:prerequisites.confirm end -->

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
  grep -E 'llm_d_epp_flow_control_queue_size|llm_d_epp_flow_control_pool_saturation|llm_d_epp_request_running'
```

Then stop the EPP port-forward. Open the query interface for the Prometheus
installation configured in the observability setup and run the query for your
chosen signal:

```promql
sum(llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
```

```promql
max(llm_d_epp_flow_control_pool_saturation{inference_pool="optimized-baseline",namespace="llm-d-optimized-baseline"})
```

```promql
sum(llm_d_epp_request_running{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
```

Each query must return a scalar or a single-element vector. Inspect the raw
series in Prometheus before continuing and update the selectors for your
deployment. The running-request metric does not expose `inference_pool`, and the
pool-saturation metric keys on `inference_pool` (the InferencePool name, i.e. the
EPP's `--pool-name`), not `service`. Scrape-time labels vary between monitoring
installations; do not copy selectors without checking the live series.

The metrics may remain at zero until requests are sent. If a series is absent,
check the Prometheus target first rather than treating absence as zero.

## Configure Prometheus Access

KEDA reads authentication Secrets from the `ScaledObject` namespace. On generic
Kubernetes the checked-in `ScaledObject` reaches the bundled kube-prometheus-stack
over plain in-cluster HTTP with no client authentication, so there is no Secret to
create. If your Prometheus endpoint requires a bearer token, mTLS, basic
authentication, or cloud workload identity, update each trigger's `serverAddress`
and add a `TriggerAuthentication` using the
[KEDA Prometheus authentication documentation](https://keda.sh/docs/2.20/scalers/prometheus/#authentication-parameters).

> [!IMPORTANT]
> KEDA's prometheus scaler ignores a `TriggerAuthentication` CA unless the trigger
> also sets `authModes`. A "CA-only" trigger (a CA parameter but no `authModes`)
> silently falls back to the system trust store and fails serving-cert
> verification with `x509: certificate signed by unknown authority`. That is why
> this guide does not enable TLS on the bundled Prometheus for the generic-k8s
> path - it uses plain in-cluster HTTP. The OpenShift path sets `authModes: bearer`,
> so its CA is honored.

### Platform notes

The Prometheus endpoint, KEDA operator namespace, and authentication method depend
on the platform. Update `KEDA_NAMESPACE`, each trigger's `serverAddress`, and any
`TriggerAuthentication` before applying the example.

#### Bundled llm-d observability stack

The checked-in `ScaledObject` targets the bundled Prometheus installation
documented in the
[observability setup guide](../../../docs/operations/observability/setup.md),
reached over plain in-cluster HTTP at its service address - nothing to configure
and no Secret to create.

To open its Prometheus query UI, keep this command running in a terminal and open
`http://localhost:9090`:

```bash
kubectl port-forward -n ${MONITORING_NAMESPACE} \
  service/llmd-kube-prometheus-stack-prometheus 9090:9090
```

#### OpenShift

OpenShift environments use the Custom Metrics Autoscaler Operator (KEDA) and
cluster monitoring through Thanos Querier. The OpenShift leaf overlays
([`ocp-queue`](optimized-baseline/ocp-queue/) and
[`ocp-saturation`](optimized-baseline/ocp-saturation/)) configure this for you -
apply one of them instead of the `k8s-*` leaves:

- Points both triggers at `thanos-querier.openshift-monitoring.svc.cluster.local:9091`
  and enables `authModes: bearer`. Thanos rejects unauthenticated queries with a
  401, and KEDA silently serves `fallback` replicas when a trigger errors, so
  unauthenticated autoscaling looks healthy while doing nothing.
- Provisions a dedicated `keda-epp-metrics-reader` ServiceAccount granted the
  `cluster-monitoring-view` ClusterRole, and adds a `keda-prometheus-auth`
  `TriggerAuthentication` pointing at that SA's token Secret. On OpenShift the
  service-ca operator injects `service-ca.crt` (the CA that signs Thanos's serving
  certificate) into the token Secret automatically, so **no CA copy is required**.

Before applying, edit the PromQL label selectors in the triggers to match your
EPP service, namespace, and model (the namespace transformer cannot rewrite the
opaque query strings). When deploying this guide to multiple namespaces on a
shared cluster, give the `keda-epp-metrics-reader-monitoring-view`
ClusterRoleBinding a namespace-unique name so the bindings do not collide.

## Choosing a Scaling Signal

This guide ships two scaling signals as overlays. Pick one; do not apply both to
the same Deployment (two ScaledObjects on one Deployment make conflicting HPAs).

- **Queue depth (default)** - `k8s-queue` / `ocp-queue`. Scales on requests
  waiting in EPP Flow Control plus running-request concurrency. The mature,
  recommended path; it carries the OCP scale-event nightly.
- **Pool saturation** - `k8s-saturation` / `ocp-saturation`. Scales on the EPP
  pool-saturation gauge plus running-request concurrency; it reacts before
  requests queue. Uses `maxReplicaCount: 10`.

  > [!WARNING]
  > **Experimental.** The saturation signal has no nightly end-to-end coverage
  > yet (the KEDA-EPP nightly exercises the queue leaf only). The manifests build
  > and share the queue path's verified base, but the signal is less
  > battle-tested. Validate thresholds against your own load before relying on it
  > in production.

Both signals originate in the EPP flow-control subsystem, so both require the
flow-control feature gate enabled by this guide's `router.values.yaml`. How that
subsystem shapes each signal, and how to set thresholds when flow control is off,
is covered in [Flow control on vs. off](#flow-control-on-vs-off) below. For the
subsystem itself, see
[EPP Flow Control](../../../docs/architecture/core/router/epp/flow-control.md).

> [!NOTE]
> This guide is validated with vLLM model servers. The flow-control signals are
> emitted by the EPP and are engine-agnostic, but the default thresholds are tuned
> for vLLM; validate them before relying on the guide with another engine.

### Saturation detector

The saturation detector estimates how loaded the inference pool is. It gates
dispatch under flow control and feeds the pool-saturation gauge, so it shapes
both signals this guide can scale on. The two detectors EPP ships define
"loaded" differently, which is exactly why the choice matters:

- **`utilization-detector` (default, recommended)** - a closed-loop detector that
  reacts to real-time telemetry (queue depth and KV-cache pressure), so it
  reflects actual memory pressure rather than just request counts. It is the EPP
  default, so this guide's `router.values.yaml` uses it without setting
  `flowControl.saturationDetector`. It has known limitations under sudden bursts
  and in heterogeneous pools; see the linked reference for those and the full
  tradeoff.
- **`concurrency-detector`** - an open-loop detector based on in-flight request
  accounting. It reacts instantly, but treats load as a raw request count, so
  high concurrency does not necessarily mean the pool is full - with prefix
  caching, for example, many concurrent requests can be cheap cache hits. It is
  also blind to KV-cache pressure, which makes it a less reliable autoscaling
  signal; prefer the default unless you have a specific reason to pin it.

See
[Saturation Detectors](../../../docs/architecture/core/router/epp/flow-control.md#saturation-detectors)
for the full comparison and each detector's tuning knobs.

## Configuration

The checked-in `ScaledObject` provides the following default configuration for
this guide:

| Parameter | Value | Tuning guidance |
|---|---|---|
| Target Deployment | `optimized-baseline-nvidia-gpu-vllm-decode` | Replace this with the Deployment to scale. |
| Minimum replicas | 1 | Increase when the deployment requires more warm capacity. |
| Maximum replicas | 8 (queue) / 10 (saturation) | Increase or decrease based on accelerator quota, cost, and desired maximum serving capacity. |
| Queue-size threshold | 1 | Decrease to react earlier to queued requests; increase if short queues are acceptable or scaling is too sensitive. |
| Pool-saturation threshold | 0.7 | Decrease to scale earlier on pool utilization; increase to tolerate higher utilization before scaling. |
| Running-request threshold | 16 | Decrease to scale earlier on active concurrency; increase if each replica can safely handle more concurrent requests within latency objectives. |
| Polling interval | 15s | Controls how often KEDA polls triggers while the target is at zero replicas. |
| Cooldown period | 300s | Controls the delay before KEDA scales the target to zero after triggers become inactive. |
| Scale-up stabilization window | 300s | Holds scale-up recommendations so a still-loading replica is not counted as unmet demand. Size near the target Deployment's cold-start time. See [Startup-time mitigation](#startup-time-mitigation). |
| Scale-down stabilization window | 300s | Holds capacity across brief demand dips before scaling in, avoiding flapping once a slow-starting replica goes Ready. See [Startup-time mitigation](#startup-time-mitigation). |

## Choosing Scaling Thresholds

The checked-in thresholds provide the default autoscaling configuration for
this guide, but they are not universal capacity values. Validate them for the
model, hardware, and serving configuration used by the target Deployment.

The queue-size trigger reacts to requests waiting in EPP Flow Control because
the current backend capacity cannot accept them. The pool-saturation trigger
reacts to how full the inference pool is, before requests begin to queue. The
running-request trigger reacts to active request concurrency before or alongside
sustained queue growth.

All triggers use `AverageValue`, so each configured threshold is interpreted
as a per-replica target by the generated HPA. For an aggregated metric, the HPA
calculates a desired replica count from the observed value and that target.
When multiple metrics are configured, the HPA evaluates each metric and uses
the largest desired replica count.

Validate the thresholds with representative load tests. Observe queue growth,
pool saturation, running-request concurrency, latency objectives, model
cold-start time, and the point at which additional replicas become useful. Set
`maxReplicaCount` high enough to provide the required capacity while respecting
accelerator quotas and cluster limits.

Do not assume that values validated for one model, accelerator type, tensor
parallel configuration, or request distribution apply to another deployment.
Future benchmarking can provide more specific recommendations for validated
model and hardware combinations.

### Flow control on vs. off

This guide enables EPP flow control, and the default thresholds assume it. Flow
control changes *where* unmet demand accumulates, which changes what each trigger
can see:

- **Flow control on (this guide).** When the pool saturates, EPP pauses dispatch
  and buffers requests in its own priority queues. Unmet demand surfaces as
  `llm_d_epp_flow_control_queue_size`, so the queue-size trigger is the primary
  scale-up signal and a low threshold (the default `1`) reacts promptly. Dispatch
  is gated per endpoint, so each replica admits only a bounded number of concurrent
  requests, and that per-endpoint ceiling sits close to the running-request
  threshold (`16`). Aggregate running requests still grow as replicas are added, but
  a single replica rarely exceeds the threshold, so the running-request trigger
  seldom trips scale-up on its own - the queue-size trigger is what drives scaling.
  Treat the running-request trigger as a keep-warm and anti-flap floor, not the
  driver.
- **Flow control off.** This guide enables flow control, so this case applies only
  if you turn it off. Both signals this guide scales on -
  `llm_d_epp_flow_control_queue_size` and `llm_d_epp_flow_control_pool_saturation` -
  are exposed *only* when the `flowControl` feature gate is on. With it off the
  series are not emitted at all, so the queue and saturation triggers resolve to
  no data and KEDA reports the metric as unavailable. The running-request
  trigger this guide already ships (`llm_d_epp_request_running`) is not gated by
  flow control and keeps working, so scaling degrades to running-requests only;
  tune its threshold accordingly, or drive scaling from a vLLM-native signal such
  as `vllm:num_requests_waiting`.

Confirm which mode you are in before tuning:

```bash
kubectl logs deployment/optimized-baseline-epp -n ${NAMESPACE} | grep "Flow Control enabled"
```

## Startup-time mitigation

Model-server pods take minutes to load a model and become `Ready`, and this
startup lag distorts autoscaling. While a new replica is still starting it does
not serve traffic, so the demand signal it was meant to relieve stays elevated.
Two problems follow: the HPA can keep scaling up and overshoot (the starting
replica is not yet reducing the signal), and then, once the pod goes `Ready` and
demand drops sharply, the HPA can scale back in immediately and flap.

The checked-in `ScaledObject` mitigates both with HPA stabilization windows in
its `behavior` block, so no extra metrics or dependencies are required:

- **`scaleUp.stabilizationWindowSeconds: 300`** holds scale-up recommendations
  over the window and acts on the most conservative one, so a replica that is
  still loading is given time to become `Ready` and relieve demand before the HPA
  adds another. Size this near the target Deployment's cold-start time: too low
  and the HPA stacks replicas while one is still coming up; too high and it reacts
  slowly to genuine sustained bursts.
- **`scaleDown.stabilizationWindowSeconds: 300`** holds capacity across brief
  demand dips, so the pool does not scale in the moment a slow-starting replica
  finally absorbs a backlog and the signal drops.

Measure your model's cold-start time (from pod scheduling to the first served
request) and set `scaleUp.stabilizationWindowSeconds` to roughly that value.

### Advanced alternative: pending-pod-aware supply

Stabilization windows are deliberately blunt: they delay all scale-up equally,
not just startup-driven overshoot. If you need demand-aware behavior, KEDA's
[`advanced.scalingModifiers`](https://keda.sh/docs/2.20/reference/scaledobject-spec/#scalingmodifiers)
can compose triggers with a `formula`. You can pair the demand trigger with a
second trigger that reports not-yet-available pods - for example a Prometheus
query over `kube-state-metrics` such as `kube_deployment_status_replicas_unavailable`
- and write a formula that discounts demand by the anticipated capacity of pods
already coming up, so the HPA does not double-count a replica it has already
requested.

This is more precise but adds cost: it depends on `kube-state-metrics` being
scraped into the same Prometheus, introduces a second trigger and a formula whose
per-pod-capacity constant must itself be tuned, and a missing series can break the
composite metric. Prefer the stabilization windows above unless you have a
specific need the windows cannot meet.

## Apply the KEDA ScaledObject

Review the base
[`scaledobject.yaml`](optimized-baseline/base/scaledobject.yaml) and your
chosen trigger component before applying. At minimum, verify these
deployment-specific fields:

- `metadata.namespace`
- `spec.scaleTargetRef.name`
- Prometheus `serverAddress`
- The PromQL label selectors
- The trigger thresholds

This walkthrough intentionally begins with one target replica so that a 1-to-N
scale-up is observable. Scale the target Deployment down before creating the
`ScaledObject`, then wait for it to become available:

<!-- guide:deploy.prepare start -->
```bash
kubectl scale deployment ${TARGET_DEPLOYMENT} -n ${NAMESPACE} --replicas=1
kubectl rollout status deployment/${TARGET_DEPLOYMENT} -n ${NAMESPACE} --timeout=15m
```
<!-- guide:deploy.prepare end -->

Apply the leaf overlay for your `TRIGGER` and `ENV`. On a generic Kubernetes
cluster with the bundled kube-prometheus-stack (plain in-cluster HTTP, no auth
secret), use the `k8s-*` leaf.

Queue signal (default):

<!-- guide:deploy.apply_k8s_queue start -->
```bash
# only when TRIGGER=queue and ENV=existing:
kubectl apply -k ${OVERLAY_ROOT}/k8s-queue
```
<!-- guide:deploy.apply_k8s_queue end -->

Saturation signal (experimental):

<!-- guide:deploy.apply_k8s_saturation start -->
```bash
# only when TRIGGER=saturation and ENV=existing:
kubectl apply -k ${OVERLAY_ROOT}/k8s-saturation
```
<!-- guide:deploy.apply_k8s_saturation end -->

On OpenShift, use the `ocp-*` leaf instead (see the [OpenShift](#openshift)
note - it points both triggers at Thanos Querier and bearer-authenticates via a
dedicated ServiceAccount; no CA copy is needed).

Queue signal (default):

<!-- guide:deploy.apply_ocp_queue start -->
```bash
# only when TRIGGER=queue and ENV=ocp:
kubectl apply -k ${OVERLAY_ROOT}/ocp-queue
```
<!-- guide:deploy.apply_ocp_queue end -->

Saturation signal (experimental):

<!-- guide:deploy.apply_ocp_saturation start -->
```bash
# only when TRIGGER=saturation and ENV=ocp:
kubectl apply -k ${OVERLAY_ROOT}/ocp-saturation
```
<!-- guide:deploy.apply_ocp_saturation end -->

## Verify KEDA Metric Evaluation

Check the `ScaledObject` status and events:

<!-- guide:verify.tests.scaledobject start -->
```bash
kubectl get scaledobject ${SCALEDOBJECT_NAME} -n ${NAMESPACE}
kubectl wait --for=condition=Ready scaledobject/${SCALEDOBJECT_NAME} -n ${NAMESPACE} --timeout=120s
```
<!-- guide:verify.tests.scaledobject end -->

`Ready=True` confirms that the scaler configuration is valid. Because this
example has `minReplicaCount: 1`, the `Active` condition is not the best signal
for 1-to-N scaling. Inspect the generated HPA's current metrics and the target
Deployment's replica count instead. `Active` becomes relevant to zero-to-one
activation in the optional scale-to-zero configuration below.

KEDA creates the HPA named in `horizontalPodAutoscalerConfig`:

<!-- guide:verify.tests.hpa start -->
```bash
kubectl get hpa ${HPA_NAME} -n ${NAMESPACE}
kubectl get hpa ${HPA_NAME} -n ${NAMESPACE} -o jsonpath='{.status.currentMetrics}' | jq
```
<!-- guide:verify.tests.hpa end -->

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

Adjust concurrency only if the reference load does not cross the configured
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

Common causes are an unreachable `serverAddress`, missing authentication (on
platforms that require it), a `TriggerAuthentication` CA that KEDA drops because
the trigger sets no `authModes`, or a PromQL query that returns more than one
element.

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

<!-- guide:cleanup start -->
```bash
# only when TRIGGER=queue and ENV=existing:
kubectl delete -k ${OVERLAY_ROOT}/k8s-queue --ignore-not-found=true

# only when TRIGGER=saturation and ENV=existing:
kubectl delete -k ${OVERLAY_ROOT}/k8s-saturation --ignore-not-found=true

# only when TRIGGER=queue and ENV=ocp:
kubectl delete -k ${OVERLAY_ROOT}/ocp-queue --ignore-not-found=true

# only when TRIGGER=saturation and ENV=ocp:
kubectl delete -k ${OVERLAY_ROOT}/ocp-saturation --ignore-not-found=true
```
<!-- guide:cleanup end -->

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
[Prometheus Adapter notes](../promadapter.md) while migrating. New EPP
autoscaling deployments should use KEDA and should not install Prometheus
Adapter solely for this guide.
