# Alerting

This page covers default sets of Prometheus alerting rules for the EPP (Endpoint Picker), for the EPP's precise prefix-cache (KV-cache) index, and for the Batch Gateway. For Prometheus and Grafana installation, see [Observability Setup](./setup.md) first, and for the metrics these alerts are built on, see [Metrics](./metrics.md).

The rules ship as a [`PrometheusRule`](https://prometheus-operator.dev/docs/getting-started/design/#prometheusrule) custom resource, so they require the Prometheus Operator (bundled with the kube-prometheus-stack installed by the [setup guide](./setup.md)).

> [!NOTE]
> Commands on this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
>
> ```bash
> export NAMESPACE=<your-llm-d-namespace>
> ```

## Prerequisites

- A running llm-d deployment with an InferencePool — see the [quickstart](../../getting-started/quickstart.md) if needed
- Prometheus and the Prometheus Operator installed — see [Observability Setup](./setup.md)
- EPP metrics being scraped — see [Metrics](./metrics.md) (verify the `epp-servicemonitor` ServiceMonitor exists)

## Step 1: Apply the Alerting Rules

Apply the bundled `PrometheusRule`:

```bash
kubectl apply -n ${NAMESPACE} -f guides/recipes/observability/alerts/epp-alerting-rules.yaml
```

> [!NOTE]
> The bundled [`install-prometheus-grafana.sh`](../../../guides/recipes/observability/install-prometheus-grafana.sh) opens Prometheus' `ruleSelector` so any `PrometheusRule` is discovered (central mode). If you run the installer in individual/scoped mode, Prometheus only selects rules carrying the `monitoring-ns: ${NAMESPACE}` label in a namespace with the same label — add that label to the `PrometheusRule` (and its namespace) to match your `ServiceMonitor`. If you bring your own Prometheus, make sure its `ruleSelector` matches the `app: epp-metrics` label on this resource.

## Step 2: Verify

Confirm the rule was created:

```bash
kubectl get prometheusrules -n ${NAMESPACE}
```

Expected output:

```text
NAME                 AGE
epp-alerting-rules   10s
```

Then open the Prometheus UI and check that the rules loaded under **Status → Rule Health** (or `http://localhost:9090/rules` after port-forwarding — see [Metrics](./metrics.md#step-6-query-metrics)). You should see the `epp.availability` and `epp.selfhealth` groups.

## Alert Reference

All metric names use the current `llm_d_epp_*` prefix. Thresholds and `for:` windows are conservative defaults — tune them for your traffic profile (see [Customization](#customization)).

### Availability and errors (`epp.availability`)

| Alert | Severity | Fires when | Why it matters |
|-------|----------|-----------|----------------|
| `EPPHighErrorRatio` | warning | Error ratio > 5% for 10m | Backend or routing failures are degrading a meaningful share of requests |
| `EPPCriticalErrorRatio` | critical | Error ratio > 20% for 5m | The inference path is likely broken, not just degraded |
| `EPPNoReadyEndpoints` | critical | `llm_d_epp_ready_endpoints == 0` for 2m | The pool has no routable endpoints — every request fails |
| `EPPMetricsAbsent` | critical | `absent(llm_d_epp_ready_endpoints)` for 5m | Coarse cluster-wide signal: no EPP metrics from any pool — every EPP is down or scraping stopped entirely (a silent observability gap) |

The error-ratio alerts are `0/0`-safe: with no traffic the expression yields no value and stays silent. On very low-volume workloads you may see startup flapping — add a request-rate floor with `and sum(rate(llm_d_epp_request_total[5m])) > N`.

> [!NOTE]
> `EPPMetricsAbsent` uses `absent()`, which only fires when *no* `llm_d_epp_ready_endpoints` series exist at all. In multi-pool deployments, one pool's EPP can die while the others keep reporting — its series vanish rather than report `0`, so neither this alert nor `EPPNoReadyEndpoints` fires for it. For per-pool coverage, alert on series that existed recently but disappeared:
>
> ```promql
> llm_d_epp_ready_endpoints offset 15m unless llm_d_epp_ready_endpoints
> ```
>
> This fires once per vanished pool, at the cost of a fixed lookback window (a pool removed on purpose also fires until the offset ages out).

### EPP self-health (`epp.selfhealth`)

| Alert | Severity | Fires when | Why it matters |
|-------|----------|-----------|----------------|
| `EPPDataLayerPollErrors` | warning | `llm_d_epp_datalayer_poll_errors_total` increasing for 10m | A data source failed to poll — scheduling may be running on stale endpoint state |
| `EPPDataLayerExtractErrors` | warning | `llm_d_epp_datalayer_extract_errors_total` increasing for 10m | A data extractor failed — scheduling may be running on stale state |
| `EPPExtProcStreamErrors` | warning | `llm_d_epp_extproc_streams_total{code!~"OK\|Canceled"}` increasing for 10m | Envoy↔EPP `ext_proc` streams are terminating abnormally |

> [!NOTE]
> `EPPExtProcStreamErrors` relies on opt-in metrics enabled by the EPP `--enable-grpc-stream-metrics` flag. When the flag is unset, the series are absent and the alert never fires. The matcher excludes `OK` and `Canceled` (a normal client disconnect) — adjust it for your environment.

### KV-cache index (`kv-cache.index`, `kv-cache.events`)

These alerts cover the precise prefix-cache pipeline embedded in the EPP: the KV-block index, the KV-events stream that feeds it from the model servers, and the `token-producer` tokenization in front of it. They ship as a separate `PrometheusRule` and are only relevant if you deployed [precise prefix-cache routing](../../../guides/precise-prefix-cache-routing/README.md) (or another config that uses the `precise-prefix-cache-producer`).

```bash
kubectl apply -n ${NAMESPACE} -f guides/recipes/observability/alerts/kv-cache-alerting-rules.yaml
kubectl get prometheusrules -n ${NAMESPACE}
```

You should then see the `kv-cache.index` and `kv-cache.events` groups under **Status → Rule Health** in the Prometheus UI.

> [!IMPORTANT]
> The `llm_d_epp_kv_cache_*` metrics are opt-in. Set `indexerConfig.kvBlockIndexConfig.enableMetrics: true` on the `precise-prefix-cache-producer` parameters (the precise prefix-cache routing guide already does). Without it, `KVCacheIndexMetricsAbsent` fires and every other rule in this group stays silent. If you bring your own Prometheus, its `ruleSelector` must match this resource's label, `app: kv-cache-metrics`.

#### Index (`kv-cache.index`)

| Alert | Severity | Fires when | Why it matters |
| --- | --- | --- | --- |
| `KVCacheIndexMetricsAbsent` | warning | `absent(llm_d_epp_kv_cache_index_lookup_requests_total)` for 10m | Index metrics are disabled or not scraped, so none of the other KV-cache alerts can fire |
| `KVCacheIndexNotAdmitting` | warning | Lookups flowing but zero `llm_d_epp_kv_cache_index_admissions_total` over 15m, for 15m | Nothing is being written to the index — the KV-events stream is broken and every lookup misses |
| `KVCachePrefixHitRateLow` | warning | Predicted prefix hit rate < 10% for 30m (with > 0.1 req/s) | The index is not finding cached prefixes — stale index, broken event stream, or a block-hash mismatch between EPP and model servers |
| `KVCacheIndexHighLookupLatency` | warning | P99 `llm_d_epp_kv_cache_index_lookup_latency_seconds` > 50ms for 10m | Lookups run on the request path, so slow lookups add directly to scheduling latency |
| `KVCacheIndexEvictionSpike` | warning | Eviction rate > 3x the previous hour's rate (and > 10 blocks/s) for 5m | Model servers are repeatedly restarting or resetting their cache; hit rate drops until the index refills |

`KVCachePrefixHitRateLow` uses `llm_d_epp_prefix_predicted_cached_tokens` / `llm_d_epp_prefix_prompt_tokens`, a true 0–1 ratio. The index counters are not: `llm_d_epp_kv_cache_index_lookup_hits_total` counts matched *blocks* and `llm_d_epp_kv_cache_index_lookup_requests_total` counts *calls*, so their quotient is blocks per lookup. Workloads with no shared prefixes legitimately sit near zero — raise the threshold or drop this alert for them.

`KVCacheIndexEvictionSpike` compares against the hour ending 15m ago, so it needs 75m of history before it can fire. A single short burst (one pod resetting its cache) clears within the 5m window and does not alert.

#### KV events and tokenization (`kv-cache.events`)

| Alert | Severity | Fires when | Why it matters |
| --- | --- | --- | --- |
| `KVCacheEventsSubscriberReconnecting` | warning | `llm_d_epp_kv_cache_events_subscriber_reconnections_total` increasing for a pod for 15m | The EPP keeps losing that pod's event stream; its cached blocks are missing from the index |
| `KVCacheEventsZMQErrors` | warning | `llm_d_epp_kv_cache_events_zmq_errors_total` increasing for a pod/operation for 15m | A ZMQ step (`bind`, `connect`, `subscribe`, `recv`, `replay-*`) is failing, so that pod's stream is incomplete |
| `KVCacheEventsStalled` | warning | A subscriber that received events before has received none for 15m while lookups continue, for 15m | Index entries for that pod are going stale |
| `KVCacheEventsPoolBacklog` | warning | `llm_d_epp_kv_cache_events_pool_queue_depth` > 1000 for 15m | Event-pool workers cannot keep up and the index lags behind the model servers; raise `kvEventsConfig.concurrency` |
| `KVCacheTokenizationSlow` | warning | > 5% of `token-producer` calls take over 100ms for 10m | Tokenization (via the render service) runs on the request path; check the render service's latency and capacity |

`KVCacheTokenizationSlow` is built on `llm_d_epp_plugin_duration_seconds{plugin_type="token-producer"}`, whose largest bucket is 100ms, so it alerts on the share of calls above that bucket rather than on a quantile (which would be capped at 100ms).

`KVCacheEventsStalled` also fires if a pod simply stops receiving requests. Treat it as a prompt to check that pod's stream, not proof that the stream is broken.

### Batch Gateway (`batch-gateway.rules`)

These alerts cover the Batch Gateway processor and GC reconciler rather than the request path, so they are shipped as a separate `PrometheusRule` and are only relevant if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md).

```bash
kubectl apply -n ${NAMESPACE} -f guides/recipes/observability/alerts/batch-gateway-alerting-rules.yaml
kubectl get prometheusrules -n ${NAMESPACE}
```

You should then see the `batch-gateway.rules` group under **Status → Rule Health** in the Prometheus UI.

> [!IMPORTANT]
> Every expression in this file is scoped with `namespace="batch-gateway"`, the namespace the Batch Gateway guide deploys into. The Batch Gateway metric names are unprefixed and generic (`jobs_processed_total`, `active_workers`), so an unscoped rule can pick up unrelated workloads. If you deployed into a different namespace, edit the matchers to match — applying the `PrometheusRule` into your namespace does **not** scope its queries. If you bring your own Prometheus, its `ruleSelector` must match this resource's label, `app: batch-gateway-metrics` (not `app: epp-metrics`, which is specific to the EPP rule).

| Alert | Severity | Fires when | Why it matters |
|-------|----------|-----------|----------------|
| `BatchGatewayHighQueueWait` | warning | p95 `job_queue_wait_duration_seconds` > 300s for 15m | Jobs are backing up in the priority queue faster than workers drain it |
| `BatchGatewayHighJobFailureRate` | warning | Failed share of `jobs_processed_total` > 10% for 10m | Job execution is failing at a rate users will notice |
| `BatchGatewayExpiredJobsDetected` | warning | Expired share of `jobs_processed_total` > 3% for 10m | Jobs are aging out before execution — capacity or completion-window problem, not a code failure |
| `BatchGatewayWorkersSaturated` | warning | `active_workers / total_workers` > 90% for 15m | The worker pool is the bottleneck; raise `NumWorkers` or add replicas |
| `BatchReconcilerErrors` | warning | `batch_reconciler_errors_total` increased over 65m | The GC orphan reconciler is failing, so orphaned jobs are not being recovered |

The two ratio alerts use `clamp_min` on the denominator, so they stay silent when no jobs are being processed rather than dividing by zero.

`BatchReconcilerErrors` uses `increase()` over a 65m window rather than `rate()`: the reconciler runs on a 60m interval by default and increments the counter at most once per cycle, which is too sparse for a `rate()` threshold. If you shorten `reconciler.interval` in the GC config, shorten the window to match — a window shorter than the interval can miss a once-per-cycle failure, and a much longer one keeps the alert firing after the error has aged out.

## Customization

These rules are a starting point, not a tuned policy. Common adjustments:

- **Thresholds and durations** — edit the `expr` comparison (`> 0.05`) and the `for:` window per alert to match your SLOs and noise tolerance.
- **Routing** — the `severity: warning|critical` labels are the hook for Alertmanager routing (e.g. page on `critical`, Slack on `warning`). Configure routes in your Alertmanager config.
- **Scope** — to alert per pool or per model, add a `by (...)` clause to the error-ratio expressions using labels such as `name` or `model_name`.

See the [PromQL Reference](./promql.md) for more queries you can promote into alerts (for example latency SLOs and flow-control saturation).

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -f guides/recipes/observability/alerts/epp-alerting-rules.yaml
kubectl delete -n ${NAMESPACE} -f guides/recipes/observability/alerts/kv-cache-alerting-rules.yaml
kubectl delete -n ${NAMESPACE} -f guides/recipes/observability/alerts/batch-gateway-alerting-rules.yaml
```

## Troubleshooting

### Rules don't appear in the Prometheus UI

1. Confirm the resource exists: `kubectl get prometheusrules -n ${NAMESPACE}`.
2. Confirm Prometheus' `ruleSelector` matches the rule's labels. The bundled installer sets `ruleSelectorNilUsesHelmValues: false` with an open `ruleSelector` in central mode; in scoped mode add the `monitoring-ns` label (see [Step 1](#step-1-apply-the-alerting-rules)).
3. Check the Prometheus Operator logs for rule-rejection errors: `kubectl logs -n llm-d-monitoring -l app.kubernetes.io/name=prometheus-operator`.

### An alert never fires

- `EPPExtProcStreamErrors` needs the EPP `--enable-grpc-stream-metrics` flag (see the note above).
- Error-ratio alerts only evaluate once there is traffic — see the `0/0`-safe note above.
- Self-health counters only produce series after the first error occurs.
- KV-cache alerts need `enableMetrics: true` on the `precise-prefix-cache-producer` (see the KV-cache section above); `KVCacheIndexMetricsAbsent` fires when it is missing.
- The per-pod KV-events counters (`messages_received`, `subscriber_reconnections`, `zmq_errors`) only produce series after the first event, reconnection or error for that pod, and are removed when the subscriber is.
