# Alerting

This page covers a default set of Prometheus alerting rules for the EPP (Endpoint Picker). For Prometheus and Grafana installation, see [Observability Setup](./setup.md) first, and for the metrics these alerts are built on, see [Metrics](./metrics.md).

The rules ship as a [`PrometheusRule`](https://prometheus-operator.dev/docs/getting-started/design/#prometheusrule) custom resource, so they require the Prometheus Operator (bundled with the kube-prometheus-stack installed by the [setup guide](./setup.md)).

> [!NOTE]
> Commands on this page use `${NAMESPACE}` for the namespace where your llm-d workload runs. Set it before following along:
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

Then open the Prometheus UI and check that the rules loaded under **Status → Rule Health** (or `http://localhost:9090/rules` after port-forwarding — see [Metrics](./metrics.md#step-5-query-metrics)). You should see the `epp.availability` and `epp.selfhealth` groups.

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

## Customization

These rules are a starting point, not a tuned policy. Common adjustments:

- **Thresholds and durations** — edit the `expr` comparison (`> 0.05`) and the `for:` window per alert to match your SLOs and noise tolerance.
- **Routing** — the `severity: warning|critical` labels are the hook for Alertmanager routing (e.g. page on `critical`, Slack on `warning`). Configure routes in your Alertmanager config.
- **Scope** — to alert per pool or per model, add a `by (...)` clause to the error-ratio expressions using labels such as `name` or `model_name`.

See the [PromQL Reference](./promql.md) for more queries you can promote into alerts (for example latency SLOs and flow-control saturation).

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -f guides/recipes/observability/alerts/epp-alerting-rules.yaml
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
