# Observability

Monitor and debug llm-d deployments with Prometheus metrics, Grafana dashboards, and OpenTelemetry distributed tracing.

> [!NOTE]
> Every well-lit path guide links here for observability setup. Install the stack once and reuse it across guides.

## Documentation

* [Set Up the Monitoring Stack](./setup.md) — Install Prometheus and Grafana, load dashboards, and deploy tracing backends
* [Collect Metrics](./metrics.md) — Scrape model server and EPP metrics into Prometheus and query them
* [Use Grafana Dashboards](./dashboards.md) — Access Grafana and import the llm-d dashboards
* [Model Server Metrics](./model-server-metrics.md) — vLLM and SGLang metric reference
* [Router (EPP) Metrics](./router-metrics.md) — llm-d Router Endpoint Picker metric reference
* [Batch Gateway Metrics](./batch-gateway-metrics.md) — Batch Gateway metric reference
* [Track Inference Cost](./inference-cost.md) — Per-model cost attribution with OpenCost
* [PromQL Query Reference](./promql.md) — Ready-to-use queries for dashboards and alerting
* [Configure Alerts](./alerting.md) — Apply the default EPP and Batch Gateway Prometheus alerting rules
* [Trace Requests](./tracing.md) — Configure OpenTelemetry across vLLM, the routing proxy, and the EPP
* [Monitor GKE TPUs](./tpu.md) — Interpret TPU hardware metrics and troubleshoot missing data

## Runnable assets

Scripts, Grafana dashboard JSON, alerting rules, and tracing manifests live in [`guides/recipes/observability/`](../../../guides/recipes/observability/) in the llm-d repository (not published as website pages).
