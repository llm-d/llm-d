# Multi-Tenant Async Processing — Quota, Priority & Saturation

An advanced [Async Processor](https://github.com/llm-d/llm-d-async) scenario built on the
[asynchronous-processing](../README.md) guide, across three dimensions — **team × tier × model**. Each
**team** gets a per-team quota (reserved vs. overflow) and a priority **tier**; each **model** gets its
own worker pool with independent **saturation-aware back-off**, observed through self-hosted
Prometheus + Grafana (or GCP Cloud Monitoring on the Pub/Sub backend).

The scenario is the point; the **message queue is a pluggable backend** — it runs unchanged on **Redis
SortedSet** (the default here) or **GCP Pub/Sub**. The gate configuration, worker pools, and scenario
walkthroughs are identical across both; only the queue wiring and how you publish differ.

![Animated architecture: two models each with their own worker pool and vLLM; within each, three team/tier lanes flow through a reserved/overflow quota gate and the tier-priority merge; model A saturates and its pool parks while model B keeps flowing](diagram/architecture.gif)

> [!NOTE]
> Source + regeneration for the diagram: [`diagram/`](diagram/) (`architecture.html` is the editable
> animated SVG).

## Overview

The demo runs **two models**, each served by its own `InferencePool` behind one gateway. Each model
gets its **own worker pool** (`model-a`, `model-b`) — so concurrency and saturation are isolated per
model — and within each pool three teams contend by **tier** and **quota**. That's **6 queues**
(3 teams × 2 models) → **2 pools**:

| Model → pool | Team | Queue (Redis) | Tier | Reserved quota | Quota prefix |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **`model-a`** | premium | `team-premium-a` | `interactive` | concurrency **2** | `quota:a:` |
| | standard | `team-standard-a` | `async` | concurrency **2** | `quota:a:` |
| | batch | `team-batch-a` | `batch` | concurrency **1** | `quota:a:` |
| **`model-b`** | premium | `team-premium-b` | `interactive` | concurrency **2** | `quota:b:` |
| | standard | `team-standard-b` | `async` | concurrency **2** | `quota:b:` |
| | batch | `team-batch-b` | `batch` | concurrency **1** | `quota:b:` |

The three dimensions:

- **Model → pool isolation.** The publisher sends a request to the `(team, model)` queue and sets
  `payload.model`; the gateway routes it to that model's `InferencePool`. Each pool has its own workers
  and its own saturation gate, so one model saturating does not park the other.
- **Team → reservation classification.** The per-team **`redis-quota`** gate runs in **`classifying`**
  mode (keyed on `metadata.team`, with a **per-model prefix** so each `(team, model)` has its own
  counter): within quota → `reserved` (org-guaranteed), over quota → `overflow` (admitted and
  deprioritized, **not** nacked).
- **Tier → priority.** A per-queue `tier` label: `interactive` (premium) > `async` (standard) > `batch`.

The [**tier-priority merge policy**](https://github.com/llm-d/llm-d-async/pull/294) runs
**per pool independently**: within each model it buckets requests into **6 strict lanes** by
`(classification, tier)`, dispatches them in order, and stamps **`x-gateway-priority`** (0 = highest …
5 = lowest):

| Lane | `x-gateway-priority` | Who (within one model) |
| :-- | :-- | :-- |
| reserved + interactive | 0 | premium within quota |
| reserved + async | 1 | standard within quota |
| reserved + batch | 2 | batch within quota |
| overflow + interactive | 3 | premium over quota |
| overflow + async | 4 | standard over quota |
| overflow + batch | 5 | batch over quota |

So within each model **all reserved traffic drains before any overflow** (org priority), tier-ordered
within each class; and the two models are fully independent. On Redis SortedSet, within a lane dispatch
is earliest-deadline-first (the deadline is the sorted-set score).

> [!NOTE]
> **Over-quota is deprioritized, not dropped.** In `classifying` mode, requests beyond a team's
> reserved quota become `overflow` and are dispatched after all `reserved` traffic, rather than
> nacked/redelivered. To hard-throttle instead, set `gate_params.gating_mode: blocking` (over-quota
> returns to the queue; backlog grows).

## Prerequisites

This guide layers on the base [asynchronous-processing](../README.md) guide — complete its
[Prerequisites](../README.md#prerequisites) first (client tools, cluster, GAIE CRDs,
[`guides/env.sh`](../../env.sh), the HF-token secret), then add the following.

- **Two model stacks behind one gateway.** The model-isolation story needs **two `InferencePool`s**
  (`POOL_A`, `POOL_B`) behind a single inference gateway that routes by `payload.model` to the matching
  pool. Bring these up by applying the [optimized-baseline](../../optimized-baseline/README.md) guide
  twice with two different models / pool names, or point the guide at your existing multi-model
  gateway.

> [!NOTE]
> **Single-model variant.** If you only have one model/pool, point both `model-a` and `model-b` at
> the same model and `InferencePool` (use the same value for `POOL_A`/`POOL_B` and `MODEL_A`/`MODEL_B`
> below). You lose the model-isolation demonstration, but the quota and tier behavior is unchanged.

- **Environment.** In addition to the base guide's variables:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export MT=${REPO_ROOT}/guides/asynchronous-processing/multitenant

  export NAMESPACE=llm-d-async
  export ASYNC_VERSION=v0.9.0          # latest llm-d-async release (includes tier-priority + classifying quota)

  # The shared inference gateway (EPP) address, and the two InferencePool + model names:
  export IP=$(kubectl get service optimized-baseline-epp -n llm-d-optimized-baseline -o jsonpath='{.spec.clusterIP}')
  export POOL_A=<pool-a> POOL_B=<pool-b>       # InferencePool names (saturation-gate scope)
  export MODEL_A=<model-a> MODEL_B=<model-b>   # served model names (go in payload.model)

  # Scenario C only: the base URL the saturation gates read PromQL from. The default
  # matches the kube-prometheus-stack install in Observability below; override it if
  # your Prometheus lives somewhere else.
  export PROM_URL=http://kps-kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090

  # Scenario C only: concurrent requests per model at which that model's pool counts as
  # saturated. Must be BELOW the pool's worker count (8 in the overlays) — see Scenario C.
  export SAT_CAP=4
  ```

## Configuration and Deployment

The value overlays live in [`values/`](values/) with literal placeholders (`NAMESPACE`, `IGW_HOST`,
`POOL_A`, `POOL_B`, `SAT_CAP` in the saturation overlays, `PROM_URL` on the self-hosted-Prometheus
saturation overlays, and — Pub/Sub only — `PROJECT_ID`). Render one for your environment before
installing:

```bash
render() {   # render <overlay-path> -> stdout
  sed -e "s/NAMESPACE/${NAMESPACE}/g" -e "s#IGW_HOST#${IP}#g" \
      -e "s/POOL_A/${POOL_A}/g" -e "s/POOL_B/${POOL_B}/g" \
      -e "s/SAT_CAP/${SAT_CAP:-4}/g" \
      -e "s#PROM_URL#${PROM_URL:-http://kps-kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090}#g" "$1"
}
```

`MODEL_A` / `MODEL_B` are **not** overlay placeholders — they never appear in a value, only in
comments. The served model names reach the system through `payload.model`, which the `publish()`
helper below fills in from `${MODEL_A}` / `${MODEL_B}`.

### Redis SortedSet (default)

The bundled Redis backs both the per-team request queues and the quota counters (the overlays point at
a `redis` service in `${NAMESPACE}`).

```bash
kubectl create namespace ${NAMESPACE}
kubectl apply -n ${NAMESPACE} -f ${MT}/manifests/redis.yaml

render ${MT}/values/redis/quota-only.yaml > /tmp/mt-redis.yaml
helm install llm-d-async \
    oci://ghcr.io/llm-d/charts/llm-d-async \
    -f /tmp/mt-redis.yaml \
    -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}

kubectl -n ${NAMESPACE} get deploy llm-d-async -o yaml | grep message-queue-impl
# -> --message-queue-impl=redis-sortedset
```

Queues are just sorted-set keys — no per-team resource creation needed; they appear on first publish.

<details>
<summary><b>GCP Pub/Sub backend</b></summary>

Requires a GCP project with the Pub/Sub API enabled and `gcloud` authenticated. `gcp-setup.sh` creates
the per-`(team, model)` topics + subscriptions, the results topic, and the service account + IAM.

<!-- llm-d-cicd:skip start -->
```bash
export PROJECT_ID=your-project
${MT}/scripts/gcp-setup.sh                       # topics, subscriptions, results topic, SA + IAM
kubectl create namespace ${NAMESPACE}
kubectl apply -n ${NAMESPACE} -f ${MT}/manifests/redis.yaml   # still needed for the quota counters

sed -e "s/NAMESPACE/${NAMESPACE}/g" -e "s#IGW_HOST#${IP}#g" -e "s/PROJECT_ID/${PROJECT_ID}/g" \
    ${MT}/values/pubsub/quota-only.yaml > /tmp/mt-pubsub.yaml
helm install llm-d-async \
    oci://ghcr.io/llm-d/charts/llm-d-async \
    -f /tmp/mt-pubsub.yaml \
    -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}
```
<!-- llm-d-cicd:skip end -->

`gcp-setup.sh` binds the `async-processor` service account to `pubsub.subscriber` + `pubsub.publisher`
+ `pubsub.viewer` (the readiness probe's `GetSubscription`) + `monitoring.viewer` (broker backlog). With
Workload Identity, follow the printed binding to map the GSA onto the chart's `llm-d-async` KSA.
</details>

> [!NOTE]
> Config-only Helm changes are read once at startup — after changing the queue/quota config, run
> `kubectl rollout restart deploy/llm-d-async -n ${NAMESPACE}`.

## Publishing requests

A request is a JSON body — `id`, `created`, `deadline`, a `payload` (a completions body whose **`model`
selects the model/pool at the gateway**), and `metadata.team` (the key the quota gate reads). Publish
to the **`(team, model)`** queue; the helper takes a team and a model (`a`|`b`).

```bash
publish() {                                   # publish <team> <a|b> [count]
  local team=$1 model=$2 n=${3:-1} ttl=${PUBLISH_TTL:-300} now dl run name i pairs=()
  [ "$model" = a ] && name="$MODEL_A" || name="$MODEL_B"
  now=$(date +%s); dl=$((now+ttl)); run="${now}-${RANDOM}"
  # The whole batch goes in one exec — ZADD takes any number of score/member pairs. One exec
  # per request trickled `publish batch a 100` in over minutes, and the workers drained it as
  # fast as it arrived: no backlog to classify as overflow. The batch shares a score, so
  # ZPopMin breaks ties on the member string — the zero-padded index makes that publish order.
  for i in $(seq 1 "$n"); do
    pairs+=("$dl" "$(printf '{"internal":{},"request_kind":"plain","data":{"id":"%s-%s-%s-%04d","created":%s,"deadline":%s,"payload":{"model":"%s","prompt":"summarize this","max_tokens":64},"metadata":{"team":"%s"}}}' \
      "$team" "$model" "$run" "$i" "$now" "$dl" "$name" "$team")")
  done
  kubectl -n ${NAMESPACE} exec -i deploy/redis -- redis-cli ZADD "team-${team}-${model}" "${pairs[@]}"
}
# e.g.  publish premium a 5    # premium team, model A; prints the number enqueued.
#   PUBLISH_TTL=900 publish batch a 400   # one deadline covers the batch — raise it if a
#                                         # saturated pool will not drain within 300s.
# Keep the count in the low thousands: every pair travels in the one exec's argv.
```

<details>
<summary><b>Publishing to GCP Pub/Sub</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
publish() {                                   # publish <team> <a|b> [count]
  local team=$1 model=$2 n=${3:-1} ttl=${PUBLISH_TTL:-300} par=${PUBLISH_PAR:-8} now dl run name i
  [ "$model" = a ] && name="$MODEL_A" || name="$MODEL_B"
  now=$(date +%s); dl=$((now+ttl)); run="${now}-${RANDOM}"
  # gcloud publishes one message per invocation, so keep `par` of them in flight: serially,
  # `publish batch a 100` takes minutes and never builds the backlog the scenarios need.
  # Each invocation is a fresh Python process — lower PUBLISH_PAR if memory is tight.
  for i in $(seq 1 "$n"); do
    gcloud pubsub topics publish "team-${team}-${model}-requests" --project "$PROJECT_ID" \
      --attribute "team=${team}" \
      --message "$(printf '{"id":"%s-%s-%s-%04d","created":%s,"deadline":%s,"payload":{"model":"%s","prompt":"summarize this","max_tokens":64},"metadata":{"team":"%s"}}' \
        "$team" "$model" "$run" "$i" "$now" "$dl" "$name" "$team")" >/dev/null &
    (( i % par )) || wait
  done
  wait
}
```
<!-- llm-d-cicd:skip end -->
</details>

## Scenarios A & B — reserved vs. overflow

**A. Steady state (one model)** — each team within its reserved quota on model A:

```bash
for t in premium standard batch; do publish "$t" a 1 & done; wait
```

Every request is within its team's quota, so all are `reserved` and dispatched in tier order
(premium→standard→batch), stamped `x-gateway-priority` 0/1/2. Read results from the per-model list:

```bash
kubectl -n ${NAMESPACE} exec deploy/redis -- redis-cli LRANGE results-a-list 0 -1   # model B -> results-b-list
```

> Each result is JSON with `id`, `payload` (the upstream response body), and `status_code` (the upstream
> HTTP status). Non-HTTP failures carry `status_code: 0` plus `error_code`/`error_message` (e.g.
> `GATE_DROPPED`, `DEADLINE_EXCEEDED`).

**B. Overflow deprioritization + model isolation** — flood `batch` on **model A** past its reserved
quota (1) while `premium` on model A and everything on model B run within quota:

```bash
publish batch   a 100 &   # far exceeds batch's reserved 1 on A -> excess is overflow (lane 5)
publish premium a 20  &   # premium reserved on A (lane 0) -> always jumps ahead
publish premium b 20  &   # model B, unaffected by A's overload
wait
```

- **Priority within model A:** batch's first concurrent request stays `reserved` (lane 2); the rest are
  `overflow` (lane 5), dispatched only after all reserved and higher-tier overflow. The
  per-`(team, model)` counter caps at the reserved limit; the excess flows as overflow (not nacked):

  ```bash
  kubectl -n ${NAMESPACE} exec deploy/redis -- redis-cli GET quota:a:team:batch     # <= 1 (model A, batch)
  kubectl -n ${NAMESPACE} exec deploy/redis -- redis-cli GET quota:b:team:premium   # model B counter, independent
  ```
- **Model isolation:** model B has its own pool and counters, so A's batch overload does not slow B —
  `results-b-list` keeps filling at model B's own rate.

## Scenario C — priority under saturation

Switch to the saturation overlay (adds the per-pool `wait-on-refuse(prometheus-query)` gates) after
bringing up [self-hosted Prometheus](#observability), then drive sustained load:

```bash
render ${MT}/values/redis/saturation-prometheus.yaml > /tmp/mt-redis-sat.yaml
grep prometheusURL /tmp/mt-redis-sat.yaml    # must be your Prometheus, not the literal PROM_URL
helm upgrade llm-d-async \
    oci://ghcr.io/llm-d/charts/llm-d-async \
    -f /tmp/mt-redis-sat.yaml -n ${NAMESPACE} --version ${ASYNC_VERSION}
```

**Confirm the gates can reach Prometheus before you read anything into the result.** The gates are
`wait-on-refuse(prometheus-query)` with `"fallback":"1"` — a budget of 1 is a wide-open gate, so an
unreachable Prometheus produces a run that looks perfect and demonstrates nothing.

```bash
# 1. The URL the gates use resolves and answers, from inside the cluster:
kubectl run --rm -i promcheck --image=curlimages/curl --restart=Never -n ${NAMESPACE} -- \
    curl -sS --max-time 5 "${PROM_URL}/api/v1/query?query=up" | head -c 120
# -> {"status":"success",...}   anything else means the gates are blind

# 2. vLLM is actually being scraped (the metric the gates read):
kubectl run --rm -i promcheck-vllm --image=curlimages/curl --restart=Never -n ${NAMESPACE} -- \
    curl -sS --max-time 5 --data-urlencode "query=sum(vllm:num_requests_running{inference_pool=\"${POOL_A}\"})" \
    "${PROM_URL}/api/v1/query" | head -c 200
# -> a result with a value; an empty "result":[] means the PodMonitor is not matching

# 3. The processor is not silently falling back:
kubectl logs -n ${NAMESPACE} deploy/llm-d-async --tail=200 | grep -i "using fallback value" \
    && echo ">>> gates are on the fallback budget (1 = wide open), not on live metrics"
```

Then drive sustained load:

```bash
publish premium a 200 & publish batch a 200 &   # heavy on model A; keep model B light
wait
```

As model A's `InferencePool` saturates, `model-a`'s budget → 0 and its workers **park in-memory
(`ActionWait`)** — so `model-a` stops pulling new work without churning the backlog, while **`model-b`
keeps dispatching at full rate** (its own gate reads only `POOL_B`). As `model-a`'s capacity frees, its
merge policy drains the highest lanes first. Query each model's budget independently:

```bash
# Assumes the kube-prometheus-stack install from Observability below; point this at
# whatever ${PROM_URL} resolves to if your Prometheus lives elsewhere.
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 &
curl -s localhost:9090/api/v1/query --data-urlencode \
  "query=clamp(1 - sum(vllm:num_requests_running{inference_pool=\"${POOL_A}\"})/${SAT_CAP}, 0, 1)"  # model-a budget -> 0
curl -s localhost:9090/api/v1/query --data-urlencode \
  "query=clamp(1 - sum(vllm:num_requests_running{inference_pool=\"${POOL_B}\"})/${SAT_CAP}, 0, 1)"  # model-b budget (~1)

# Parked workers hold their message instead of dispatching, so model-a's in-flight count
# hovers near ${SAT_CAP} while model-b's climbs to its 8 workers:
curl -s localhost:9090/api/v1/query --data-urlencode \
  "query=sum by (pool_name) (llm_d_async_async_inflight_requests)"
```

**What "saturated" should look like:** under this load `model-a`'s budget reaches **exactly `0`** and
stays there in stretches, while `model-b` sits at `~1`. If `model-a` never reaches 0, the scenario is
not actually happening — nothing parks, and the run still completes and looks healthy. Check `SAT_CAP`
against the sizing rule below before concluding the gate worked.

> [!IMPORTANT]
> **`SAT_CAP` must be smaller than the pool's `workers`.** `prometheus-query` closes its gate at
> budget `<= 0`, and `clamp(..., 0, 1)` floors the budget at 0 — so the gate closes only once
> `SAT_CAP` requests are running on that model. Scenario C's load is entirely async, so the only
> thing driving that count is the pool's own workers (`8` per model in the overlays), and a worker
> evaluates the gate while holding a message it has not dispatched yet: at most `workers - 1` of the
> pool's requests are running at that moment. Set `SAT_CAP` at or above `workers` and the budget can
> never reach 0. The default `SAT_CAP=4` leaves margin on two counts: `vllm:num_requests_running`
> counts only requests the model server is actively running, not ones waiting in its queue, and the
> gate reads it through a 15s `PodMonitor` scrape plus `prometheusCacheTTL: 5s`, so the count it acts
> on is up to ~20s behind the pool.
>
> In production the divisor is a capacity number, not a demo knob: size it to the pool's real
> concurrent-request capacity (`ready pods × per-pod concurrency`) and give the pool enough workers
> to reach it. The gate is back-pressure against **all** traffic on the pool — including synchronous
> clients — so there the count is not bounded by this processor's workers.

## Observability

Self-hosted Prometheus + Grafana works on any cluster and the gates query it in **real time**; it is
the path for the Redis backend. The chart ships a `PodMonitor`, `PrometheusRule`, and Grafana
dashboard; the saturation overlays turn them on.

```bash
# 1. Prometheus Operator + Prometheus + Grafana
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f ${MT}/values/kube-prometheus-stack.yaml

# 2. Scrape the vLLM model server (adjust selector/namespace/port in the manifest)
kubectl apply -f ${MT}/manifests/prometheus-vllm-podmonitor.yaml
```

Open Grafana (`admin`/`admin` in the demo values) and run the Scenario-C load; the **Async Processor**
dashboard shows `async_dispatch_budget`, `async_inflight_requests`, `async_gate_decisions_total`, and
`async_broker_backlog{queue_name,pool_name}`. Break panels down by **`pool_name`** (`model-a` /
`model-b`) for the per-model view and by **`queue_name`** for the per-team-per-model view.
`async_dispatch_budget` is the **queue** gates' budget (the per-team quota gates), so it says nothing
about the per-pool saturation gates. Those report through `async_gate_metric_value` — the value the
gate last read, i.e. the `clamp(...)` result — against `async_gate_metric_threshold`, which the gate
closes at (`value <= threshold`, and `prometheus-query` pins the threshold to `0`). Both are labelled
by the owning `pool_name`:

```promql
llm_d_async_async_gate_metric_value{pool_name="model-a"}       # -> 0 while the pool is parked
llm_d_async_async_gate_metric_threshold{pool_name="model-a"}   # -> 0
```

Their absence is itself a signal: the gauges are only written on a **successful** read, so a missing
or frozen `async_gate_metric_value` means the gate is running on its fallback budget. Cross-check
against Prometheus directly as in [Scenario C](#scenario-c--priority-under-saturation).

<details>
<summary><b>GCP Cloud Monitoring (Pub/Sub on GKE)</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} -f ${MT}/manifests/gmp-podmonitoring.yaml    # AP metrics -> Cloud Monitoring
gcloud monitoring dashboards create --project ${PROJECT_ID} \
  --config-from-file=${MT}/dashboards/cloud-monitoring.json

# For the gates' in-cluster PromQL reads on Pub/Sub (option A), deploy the GMP query frontend and
# upgrade to the GMP saturation overlay:
kubectl apply -n ${NAMESPACE} -f ${MT}/manifests/gmp-frontend.yaml
sed -e "s/NAMESPACE/${NAMESPACE}/g" -e "s#IGW_HOST#${IP}#g" -e "s/POOL_A/${POOL_A}/g" \
    -e "s/POOL_B/${POOL_B}/g" -e "s/PROJECT_ID/${PROJECT_ID}/g" \
    ${MT}/values/pubsub/saturation-gmp.yaml > /tmp/mt-pubsub-sat.yaml
helm upgrade llm-d-async oci://ghcr.io/llm-d/charts/llm-d-async \
  -f /tmp/mt-pubsub-sat.yaml -n ${NAMESPACE} --version ${ASYNC_VERSION}
```
<!-- llm-d-cicd:skip end -->

The `PodMonitoring` ingests the AP metrics; the dashboard charts request/success rate, in-flight, p95
latency, plus **Pub/Sub backlog per team**. The gate-metric panels need an image newer than v0.7.2. GMP
/ Monarch lags real time ~1–2 min, so gate control is bang-bang on that timescale; the self-hosted
Prometheus path reacts within one scrape.
</details>

## Notes & gotchas

- **Image / version.** The overlays no longer pin an image tag — the image tracks the chart's
  `appVersion`, selected by `--version ${ASYNC_VERSION}`. Use a release whose app image actually exists
  (v0.7.4+).
- **Reserved quota vs. pool size (per model).** Each team's quota is its *reserved* capacity (priority
  lane) in `classifying` mode, not a hard cap — over-quota flows as `overflow`. Within each model pool,
  keep the **sum** of that model's reserved quotas at or below the pool's worker count.
- **Per-model quota counters** are keyed `quota:<a|b>:team:<team>`, so a team's reserved capacity on
  model A is independent of its capacity on model B.
- **Saturation gate.** These overlays use `prometheus-query` over `vllm:num_requests_running`. The
  `prometheus-saturation` gate instead expects the EPP metric
  `inference_extension_flow_control_pool_saturation`.
- **Saturation divisor vs. pool size.** `SAT_CAP` is the concurrency at which a model counts as
  saturated, and the gate closes only when the budget hits 0 — i.e. only once `SAT_CAP` requests are
  running. Keep it **below** that pool's `workers`, or async load alone can never close the gate; see
  [Scenario C](#scenario-c--priority-under-saturation).
- **An unreachable Prometheus fails open, not closed.** The saturation gates set `"fallback":"1"`, and
  a budget of 1 is a fully open gate. If `PROM_URL` is wrong, or the vLLM `PodMonitor` matches nothing,
  Scenario C completes cleanly and demonstrates nothing — no error, no parked pool. Run the three
  checks in [Scenario C](#scenario-c--priority-under-saturation) before drawing conclusions from a run.

## Cleanup

```bash
helm uninstall llm-d-async -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -f ${MT}/manifests/redis.yaml
# self-hosted Prometheus/Grafana:
kubectl delete -f ${MT}/manifests/prometheus-vllm-podmonitor.yaml
helm uninstall kps -n monitoring && kubectl delete ns monitoring
```

<details>
<summary><b>GCP Pub/Sub cleanup</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -n ${NAMESPACE} -f ${MT}/manifests/gmp-frontend.yaml -f ${MT}/manifests/gmp-podmonitoring.yaml
gcloud monitoring dashboards list --project ${PROJECT_ID} --filter='displayName:"Async Processor"' \
  --format='value(name)' | xargs -r -n1 gcloud monitoring dashboards delete --project ${PROJECT_ID} --quiet
PROJECT_ID=${PROJECT_ID} DELETE_SA=1 ${MT}/scripts/gcp-teardown.sh
```
<!-- llm-d-cicd:skip end -->
</details>
