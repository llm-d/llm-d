# Autoscaling Workloads with KEDA and EPP Metrics

This guide configures [KEDA](https://keda.sh/) to scale an llm-d model server
Deployment from demand signals emitted by the Endpoint Picker (EPP). KEDA is the
recommended and user-facing autoscaling path described here.

This guide scales on EPP demand signals - queue depth or pool saturation - not CPU
or GPU utilization, which an inference accelerator pins high at both low and high
concurrency.

For how KEDA+EPP scaling works and why demand signals beat utilization, see
[KEDA with EPP Metrics](../../../docs/architecture/advanced/autoscaling/keda-epp.md#the-llm-autoscaling-problem).

## Prerequisites

1. Complete the [optimized-baseline guide](../../optimized-baseline/README.md),
   including
   [enabling monitoring](../../optimized-baseline/README.md#3-observability--troubleshooting).
   Confirm that Prometheus is scraping the EPP metrics endpoint before
   configuring autoscaling.

Set the guide environment variables. `SIGNAL` selects the scaling signal
(`queue` or `saturation`) and `ENV` selects the platform (`existing` or `ocp`);
together they name the overlay you apply.

The deployment-specific variables are rendered into the overlay with `envsubst`
at apply time, so install `envsubst` (shipped with GNU gettext; `brew install
gettext` on macOS, where it is not present by default). The defaults below match
the stock optimized-baseline deployment, so if you followed that guide unchanged
you can leave them as-is.
When adapting the guide to your own deployment you typically change only two of
them, `NAMESPACE` and `MODEL`; the other three follow from the Helm release
name (and accelerator) you chose at install time:

- `NAMESPACE` - the namespace you deployed into.
- `MODEL` - the model you serve; must match the `model_name` label on the EPP
  metrics.
- `EPP_SERVICE` - the EPP service name, `<release>-epp`.
- `INFERENCE_POOL` - the InferencePool name, which equals `<release>`.
- `TARGET_DEPLOYMENT` - the decode Deployment the ScaledObject targets,
  `<release>-<accelerator>-vllm-decode` (for the stock release,
  `optimized-baseline-nvidia-gpu-vllm-decode`).

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-optimized-baseline
export MONITORING_NAMESPACE=llm-d-monitoring
export KEDA_NAMESPACE=keda # options: keda, openshift-keda
export MODEL=Qwen/Qwen3-32B
export TARGET_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
export SCALEDOBJECT_NAME=optimized-baseline-keda-epp
export HPA_NAME=keda-hpa-optimized-baseline
export EPP_SERVICE=optimized-baseline-epp
export INFERENCE_POOL=optimized-baseline
export SIGNAL=queue # options: queue, saturation
export ENV=existing # options: existing, ocp
export OVERSHOOT=windows # options: windows, guard
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
kubectl logs deployment/optimized-baseline-epp -c epp -n ${NAMESPACE} | grep "Initializing Flow Control layer"
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
> KEDA's prometheus scaler drops a `TriggerAuthentication` CA unless the trigger also
> sets `authModes`; a CA-only trigger fails serving-cert verification with `x509:
> certificate signed by unknown authority`. The generic-k8s path uses plain
> in-cluster HTTP for this reason; the OpenShift path sets `authModes: bearer`.

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
([`overlays/ocp/queue`](optimized-baseline/overlays/ocp/queue/) and
[`overlays/ocp/saturation`](optimized-baseline/overlays/ocp/saturation/)) configure
this for you - apply one of them instead of the `overlays/k8s/*` leaves:

- Points both triggers at `thanos-querier.openshift-monitoring.svc.cluster.local:9091`
  and enables `authModes: bearer`. Without it Thanos rejects the query (401 when
  unauthenticated, 403 when the ServiceAccount lacks `cluster-monitoring-view`), the
  trigger errors, and the ScaledObject goes `Ready=False` with `KEDAScalerFailed`
  events. No `fallback` is configured, so autoscaling fails visibly rather than
  looking healthy while doing nothing.
- Provisions a dedicated `keda-epp-metrics-reader` ServiceAccount granted the
  `cluster-monitoring-view` ClusterRole, and adds a `keda-prometheus-auth`
  `TriggerAuthentication` pointing at that SA's token Secret. On OpenShift the
  service-ca operator injects `service-ca.crt` (the CA that signs Thanos's serving
  certificate) into the token Secret automatically, so **no CA copy is required**.

The overlays carry `${...}` placeholders for the namespace, model, target
deployment, EPP service, and inference pool, so you set those as environment
variables and the apply step renders them with `envsubst` - no manual YAML edits.
The OCP leaf also renders the metrics-reader ClusterRoleBinding subject namespace
from `${NAMESPACE}`, so the binding follows your deployment namespace. Because the
ClusterRoleBinding is cluster-scoped, the OCP leaf also renders its name as
`keda-epp-metrics-reader-monitoring-view-${NAMESPACE}`, so deploying to multiple
namespaces on a shared cluster does not make the bindings collide - no manual
rename is needed.

## Choose Your Path

Pick one signal and one overshoot strategy, set the matching environment variables,
and apply the leaf overlay below. Do not apply two `ScaledObject`s to one Deployment.

| `SIGNAL` | `OVERSHOOT` | Leaf overlay | Use when |
|---|---|---|---|
| `queue` (default) | `windows` (default) | `overlays/{k8s,ocp}/queue` | Mature, nightly-covered path; portable startup smoothing. |
| `queue` | `guard` | `overlays/{k8s,ocp}/queue-guard` | You need scaleUp=0 and have `kube-state-metrics` scraped. |
| `saturation` (experimental) | `windows` | `overlays/{k8s,ocp}/saturation` | Scale before requests queue; validate thresholds first. |

Signal background and the saturation-detector choice: [Scaling Signals](../../../docs/architecture/advanced/autoscaling/keda-epp.md#scaling-signals) and [Saturation Detector](../../../docs/architecture/advanced/autoscaling/keda-epp.md#saturation-detector). The saturation signal has no nightly end-to-end coverage yet; validate it against your own load before production use.

> [!NOTE]
> This guide is validated with vLLM model servers. The flow-control signals are
> emitted by the EPP and are engine-agnostic, but the default thresholds are tuned
> for vLLM; validate them before relying on the guide with another engine.

## Configuration

The checked-in `ScaledObject` provides the following default configuration for
this guide:

| Parameter | Value |
|---|---|
| Target Deployment | `optimized-baseline-nvidia-gpu-vllm-decode` |
| Minimum replicas | 1 |
| Maximum replicas | 8 (queue) / 10 (saturation) |
| Queue-size threshold | 1 |
| Pool-saturation threshold | 0.7 |
| Running-request threshold | 16 |
| Polling interval | 15s |
| Cooldown period | 300s |
| Scale-up stabilization window | 300s |
| Scale-down stabilization window | 300s |

For tuning guidance on each value, see [KEDA with EPP Metrics](../../../docs/architecture/advanced/autoscaling/keda-epp.md).

For how thresholds are interpreted (per-replica `AverageValue` targets) and how to validate them, see [Dual-Metric Strategy](../../../docs/architecture/advanced/autoscaling/keda-epp.md#dual-metric-strategy).

For how flow-control on/off changes which trigger sees demand, see [Flow Control On vs. Off](../../../docs/architecture/advanced/autoscaling/keda-epp.md#flow-control-on-vs-off).

## Overshoot While Pods Start (optional)

New replicas take minutes to load a model, so scale-up can overshoot. The default
`OVERSHOOT=windows` smooths this with HPA stabilization windows. To opt into the
demand-aware guard (queue signal only), set `OVERSHOOT=guard`, which credits the
not-yet-Ready replicas against demand and lets scale-up act immediately. To learn how each works and
when to pick which, see [Overshoot and Startup-Time Mitigation](../../../docs/architecture/advanced/autoscaling/keda-epp.md#overshoot-and-startup-time-mitigation).

## Apply the KEDA ScaledObject

Review the ScaledObject for your platform and signal before applying. Each overlay
carries a full
[`scaledobject.yaml`](optimized-baseline/overlays/k8s/queue/scaledobject.yaml) (the
link points at the generic-Kubernetes queue overlay). The namespace, target
deployment, and the PromQL label selectors are rendered from the environment
variables in the export block above by `envsubst` at apply time, so the fields to
review and adjust directly in the YAML are:

- Prometheus `serverAddress` (the bundled kube-prometheus-stack on generic
  Kubernetes; the OCP overlays point it at Thanos Querier)
- The trigger thresholds (and, for the guard overlay, the matching per-replica
  constants in the `scalingModifiers` formula)

This walkthrough intentionally begins with one target replica so that a 1-to-N
scale-up is observable. Scale the target Deployment down before creating the
`ScaledObject`, then wait for it to become available:

<!-- guide:deploy.prepare start -->
```bash
kubectl scale deployment ${TARGET_DEPLOYMENT} -n ${NAMESPACE} --replicas=1
kubectl rollout status deployment/${TARGET_DEPLOYMENT} -n ${NAMESPACE} --timeout=15m
```
<!-- guide:deploy.prepare end -->

Apply the leaf overlay for your `SIGNAL` and `ENV`. Each apply builds the leaf,
renders its `${...}` placeholders with `envsubst`, and pipes the result to
`kubectl apply`.

### Platform specifics

The apply command differs by platform: the overlay path (`overlays/k8s/*` vs
`overlays/ocp/*`) and the Prometheus auth are not the same on a generic cluster
and on OpenShift. Pick the block for your `ENV`.

#### Generic Kubernetes

On a generic Kubernetes cluster with the bundled kube-prometheus-stack (plain
in-cluster HTTP, no auth secret), use the `overlays/k8s/*` leaf.

Queue signal (default):

<!-- guide:deploy.apply_k8s_queue start -->
```bash
# only when SIGNAL=queue and ENV=existing and OVERSHOOT=windows:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/queue | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
```
<!-- guide:deploy.apply_k8s_queue end -->

Queue signal with the overshoot guard (`OVERSHOOT=guard`): same signal, but the
overshoot-aware `scalingModifiers` formula replaces the scale-up stabilization
window (see [Overshoot and Startup-Time Mitigation](../../../docs/architecture/advanced/autoscaling/keda-epp.md#overshoot-and-startup-time-mitigation)).

<!-- guide:deploy.apply_k8s_queue_guard start -->
```bash
# only when SIGNAL=queue and ENV=existing and OVERSHOOT=guard:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/queue-guard | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
```
<!-- guide:deploy.apply_k8s_queue_guard end -->

Saturation signal (experimental):

<!-- guide:deploy.apply_k8s_saturation start -->
```bash
# only when SIGNAL=saturation and ENV=existing:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/saturation | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
```
<!-- guide:deploy.apply_k8s_saturation end -->

#### OpenShift

On OpenShift, use the `overlays/ocp/<signal>` leaf instead (see the [OpenShift](#openshift)
note - it points both triggers at Thanos Querier and bearer-authenticates via a
dedicated ServiceAccount; no CA copy is needed).

Queue signal (default):

<!-- guide:deploy.apply_ocp_queue start -->
```bash
# only when SIGNAL=queue and ENV=ocp and OVERSHOOT=windows:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/queue | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
```
<!-- guide:deploy.apply_ocp_queue end -->

Queue signal with the overshoot guard (`OVERSHOOT=guard`): the guard rewrites the
trigger list, so this leaf bearer-authenticates all three triggers against Thanos
(see [Overshoot and Startup-Time Mitigation](../../../docs/architecture/advanced/autoscaling/keda-epp.md#overshoot-and-startup-time-mitigation)).

<!-- guide:deploy.apply_ocp_queue_guard start -->
```bash
# only when SIGNAL=queue and ENV=ocp and OVERSHOOT=guard:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/queue-guard | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
```
<!-- guide:deploy.apply_ocp_queue_guard end -->

Saturation signal (experimental):

<!-- guide:deploy.apply_ocp_saturation start -->
```bash
# only when SIGNAL=saturation and ENV=ocp:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/saturation | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl apply -f -
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

> [!NOTE]
> These load commands target the EPP service directly (`${EPP_SERVICE}:80`). That
> path is valid only in standalone/`epponly` router mode, where the EPP pod carries
> the envoy-proxy sidecar (the topology the nightly exercises). In gateway mode
> (agentgateway/istio/gke), send inference through `llm-d-inference-gateway` instead;
> the EPP service `:80` returns 404.

Run a temporary curl pod in the workload namespace:

```bash
kubectl run curl-load --rm -it \
  --image=curlimages/curl \
  --restart=Never \
  --namespace=${NAMESPACE} \
  --env=MODEL=${MODEL} \
  --env=EPP_SERVICE=${EPP_SERVICE} -- sh
```

From inside the pod, send a bounded set of concurrent requests:

```bash
cat > /tmp/request.json <<EOF
{
  "model": "${MODEL}",
  "prompt": "Write a detailed explanation of how continuous batching works.",
  "max_tokens": 256
}
EOF

seq 1 100 | xargs -P 16 -I{} \
  curl -sS --max-time 180 -o /dev/null -w '%{http_code}\n' \
    -X POST http://${EPP_SERVICE}/v1/completions \
    -H 'Content-Type: application/json' \
    --data-binary @/tmp/request.json
```

Adjust concurrency only if the reference load does not cross the configured
threshold. Keep request counts and timeouts bounded while tuning.

With `OVERSHOOT=guard` the reference load above exercises scale-up the same way it
does for the windows path: the guard combines queue depth and running requests in its
formula, so moderate concurrency drives scale-up through the running-request term
before any queue forms. To exercise the formula's queue-driven branch specifically -
where demand comes from a backlog rather than from concurrency - raise `-P`
(concurrency) and `max_tokens` until `llm_d_epp_flow_control_queue_size` goes
non-zero. The queue only forms once load exceeds a replica's serving capacity, and
the level needed depends on the model, accelerator, tensor-parallel degree, and
`max-model-len`, so climb from a few hundred concurrent requests rather than assuming
a fixed value. See
[Overshoot and Startup-Time Mitigation](../../../docs/architecture/advanced/autoscaling/keda-epp.md#overshoot-and-startup-time-mitigation).

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
# only when SIGNAL=queue and ENV=existing and OVERSHOOT=windows:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/queue | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -

# only when SIGNAL=queue and ENV=existing and OVERSHOOT=guard:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/queue-guard | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -

# only when SIGNAL=saturation and ENV=existing:
kubectl kustomize ${OVERLAY_ROOT}/overlays/k8s/saturation | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -

# only when SIGNAL=queue and ENV=ocp and OVERSHOOT=windows:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/queue | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -

# only when SIGNAL=queue and ENV=ocp and OVERSHOOT=guard:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/queue-guard | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -

# only when SIGNAL=saturation and ENV=ocp:
kubectl kustomize ${OVERLAY_ROOT}/overlays/ocp/saturation | envsubst '$NAMESPACE $MODEL $TARGET_DEPLOYMENT $EPP_SERVICE $INFERENCE_POOL' | kubectl delete --ignore-not-found=true -f -
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

For how zero-to-one activation, `cooldownPeriod`, and cold-start latency behave, see [Scale to Zero](../../../docs/architecture/advanced/autoscaling/keda-epp.md#scale-to-zero).

## Legacy Prometheus Adapter Path

Existing direct-HPA deployments can refer to the
[Prometheus Adapter notes](../promadapter.md) while migrating. New EPP
autoscaling deployments should use KEDA and should not install Prometheus
Adapter solely for this guide.
