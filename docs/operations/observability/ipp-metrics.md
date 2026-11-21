# Inference Payload Processor Metrics

Use this reference and the **llm-d Inference Payload Processor** Grafana dashboard when your deployment includes an Inference Payload Processor (IPP). IPP runs before model-server endpoint selection and can extract request fields, choose a model, and modify routing headers. Its processing signals complement the [router](./router-metrics.md) and [model server](./model-server-metrics.md) metrics.

The names, types, and intrinsic labels below match the [IPP metric definitions](https://github.com/llm-d/llm-d-inference-payload-processor/blob/373c6b25056a848be71792395ce4add108d9d682/pkg/metrics/metrics.go) and [component reference](https://github.com/llm-d/llm-d-inference-payload-processor/blob/373c6b25056a848be71792395ce4add108d9d682/docs/metrics.md). These metrics have ALPHA stability. Check the source for your deployed image before comparing a different release.

## Scrape prerequisites

IPP serves `/metrics` on port `9090` by default, independently of the ext-proc gRPC port (`9004`) and health port (`9005`). `--metrics-port` changes that port. `--metrics-endpoint-auth=true` is the default: the collector must present authorized Kubernetes credentials, and IPP must have the permissions required for delegated authentication and authorization. A `401` or `403` response is an authentication or authorization failure, not evidence of zero traffic.

For an existing deployment, confirm the collector can reach the metrics listener with its configured credentials and that the target is `UP` before loading the dashboard. Preserve the endpoint's authentication.

The [IPP chart at the referenced revision](https://github.com/llm-d/llm-d-inference-payload-processor/blob/373c6b25056a848be71792395ce4add108d9d682/config/charts/payload-processor/templates/payload-processor.yaml) exposes only the ext-proc Service port; it does not expose a named metrics Service port. The [chart RBAC](https://github.com/llm-d/llm-d-inference-payload-processor/blob/373c6b25056a848be71792395ce4add108d9d682/config/charts/payload-processor/templates/rbac.yaml) covers ConfigMap access, not metrics authentication.

Configure metrics discovery, credentials, and the associated permissions for your installation rather than pointing a model-server PodMonitor at the ext-proc Service.

The dashboard expects the collector to attach `namespace` and `pod` target labels. Those labels identify the scraped process; IPP does not emit them as intrinsic metric labels. If your collector uses different labels, relabel the targets or adapt the dashboard queries. This page does not install IPP or change an existing deployment's serving or authentication configuration.

## Metric reference

| Metric | Type | Intrinsic labels | Interpretation |
| --- | --- | --- | --- |
| `ipp_info` | Gauge | `commit`, `build_ref` | Build identity; the sample value is `1`. Useful for selecting targets and identifying mixed builds. |
| `ipp_success_total` | Counter | None | Successful IPP processing. It is not an end-to-end inference-success counter or a total-request denominator. |
| `ipp_body_field_not_found_total` | Counter | `field` | A configured field was absent from the request body. Check the field name and request schema. |
| `ipp_body_field_empty_total` | Counter | `field` | The field was present but empty. Distinguish this from a missing field when debugging a routing-header plugin. |
| `ipp_plugin_duration_seconds` | Histogram | `extension_point`, `plugin_type`, `plugin_name` | Processing time for each configured plugin and extension point. |
| `ipp_model_selector_e2e_duration_seconds` | Histogram | None | Time spent in model selection. Compare with plugin latency to localize selection overhead. |
| `ipp_model_selector_attempt_total` | Counter | `status` | Selection attempts with `status="success"` or `status="failure"`; a failure can mean no models survived filtering. |
| `ipp_request_ttft_seconds` | Histogram | `model` | Observed time to first token, grouped by the selected model. The label is `model`, not `model_name`. |

Use `rate()` or `increase()` for counters and histogram buckets. The plugin and selector latency buckets cover `0.0001` to `0.1` seconds before the `+Inf` bucket; TTFT buckets cover `0.05` to `30` seconds. Quantiles near the highest finite bucket have limited resolution for slower observations.

There is no general IPP request-error counter in this source contract. Do not calculate an inference error rate as `1 - ipp_success_total / ipp_model_selector_attempt_total`: processing successes and selector attempts measure different operations, and not every pipeline runs model selection. Compare IPP failures with gateway responses, router errors, and model-server outcomes.

## Dashboard

Load [llm-d-inference-payload-processor.json](../../../guides/recipes/observability/grafana/dashboards/llm-d-inference-payload-processor.json) with the existing [dashboard loader](./dashboards.md#import-dashboards). The loader discovers the new JSON file automatically.

Select the Prometheus datasource, namespace, and pod. The dashboard shows:

- IPP processing success rate and model-selection attempts by status.
- Model-selector and plugin p99 processing latency.
- Missing and empty body fields, separated by field name.
- TTFT p99 by model, with a model filter that applies to the TTFT panel.
- Build information for the selected IPP pods.

An absent panel can mean that the corresponding plugin or model-selection path has not run. Start with `ipp_info` and the collector's target health to distinguish an absent metric from a failed scrape. The success counter has no model label, so selecting a TTFT model does not filter the overall success or plugin panels.

For shared monitoring setup and dashboard import troubleshooting, see [Set Up the Monitoring Stack](./setup.md) and [Use Grafana Dashboards](./dashboards.md).
