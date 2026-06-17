# Observability

Monitor and debug llm-d deployments with Prometheus metrics, Grafana dashboards, and OpenTelemetry distributed tracing.

> [!NOTE]
> Every well-lit path guide links here for observability setup. Install the stack once and reuse it across guides.

## Documentation

* [Setup](./setup.md) — Install Prometheus and Grafana, load dashboards, and deploy tracing backends
* [Metrics](./metrics.md) — Enable and interpret model server and EPP metrics
* [Distributed Tracing](./tracing.md) — Configure OpenTelemetry across vLLM, the routing proxy, and the EPP
* [PromQL Reference](./promql.md) — Ready-to-use queries for dashboards and alerting

## Runnable assets

Scripts, Grafana dashboard JSON, and tracing manifests live in [`guides/recipes/observability/`](../../../guides/recipes/observability/) in the llm-d repository (not published as website pages).
