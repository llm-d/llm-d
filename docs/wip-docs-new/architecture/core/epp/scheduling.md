# EPP Scheduling


### Plugin Types

The EPP pipeline is composed of four types of plugins, each serving a distinct role:

#### Handlers

Handler plugins prepare the request context before filtering and scoring. They run first in the pipeline and set up the state that downstream plugins depend on.

| Plugin | Description |
|--------|-------------|
| `tokenizer` | Tokenizes the request prompt, enabling cache-aware scorers to compare token sequences across endpoints. |
| `single-profile-handler` | Routes all requests through a single scheduling profile (the default for non-disaggregated deployments). |
| `pd-profile-handler` | Orchestrates prefill/decode disaggregation by selecting the appropriate scheduling profile and coordinating the two-phase request flow. |
| `prefill-header-handler` | Manages prefill request headers and tracking for disaggregated serving. |

#### Filters

Filter plugins remove ineligible endpoints from consideration. They execute after handlers and before scoring.

| Plugin | Description |
|--------|-------------|
| `prefill-filter` | Retains only endpoints capable of serving prefill requests. Used in disaggregated serving. |
| `decode-filter` | Retains only endpoints capable of serving decode requests. Used in disaggregated serving. |

#### Scorers

Scorer plugins assign a numerical score to each candidate endpoint. Multiple scorers run independently, and their scores are combined using configurable weights. The final score for an endpoint is computed as:

```
Final Score = Σ (scorer_weight × scorer_score)
```

The `max-score-picker` then selects the endpoint with the highest final score.

**Available Scorers:**

| Scorer | Description | Parameters |
|--------|-------------|------------|
| `prefix-cache-scorer` | Tracks request traffic patterns to estimate which endpoints are likely to have relevant prefix cache entries in GPU memory. Does not require direct introspection of the model server. | `maxPrefixBlocksToMatch`, `lruCapacityPerServer`, `autoTune` |
| `precise-prefix-cache-scorer` | Provides real-time, precise prefix cache awareness by subscribing to vLLM's KV-Events stream. Requires a tokenizer sidecar and ZMQ connectivity. | `tokenProcessorConfig.blockSize`, `indexerConfig.speculativeIndexing`, `kvEventsConfig.zmqEndpoint`, `kvEventsConfig.concurrency`, `kvEventsConfig.discoverPods` |
| `kv-cache-utilization-scorer` | Scores based on current KV-cache memory utilization. Lower utilization scores higher. | None |
| `queue-scorer` | Scores based on request queue depth. Shorter queues score higher. | None |
| `active-request-scorer` | Scores based on in-flight request count. | None |
| `slo-scorer` | (Experimental) Uses latency predictor sidecars to estimate per-endpoint response latency and scores based on SLO targets. | See [Latency Predictor](../../components/advanced/latency-predictor.md) |

Different deployment scenarios call for different scorer combinations. Higher weights mean the scorer has more influence on the final endpoint selection. Tuning weights lets you trade off between objectives -- for example, increasing `prefix-cache-scorer` weight favors cache locality at the potential cost of queue imbalance.

#### Pickers

Picker plugins make the final endpoint selection based on the aggregated scores.

| Plugin | Description |
|--------|-------------|
| `max-score-picker` | Selects the endpoint with the highest weighted score. This is the default picker for most deployments. |
| `random-picker` | Selects a random endpoint. Used as a fallback or in scenarios like wide expert-parallelism where fine-grained scoring is not yet supported. |

### Flow Control

Flow control is an optional feature that prevents backend overload by buffering requests at the gateway when model servers are saturated. Without flow control, the EPP always routes immediately to the best available endpoint, even if all endpoints are under heavy load.

When flow control is enabled:

1. The EPP monitors backend saturation using configurable thresholds.
2. If all endpoints are saturated, incoming requests are queued at the gateway rather than being immediately dispatched.
3. As endpoints become available, queued requests are released in order.
4. Queue depth is exposed via the `inference_extension_flow_control_queue_size` Prometheus metric, which can drive HPA-based autoscaling.

### Monitoring

The EPP exposes Prometheus metrics for observability. Key metrics include:

- Request scheduling latency and throughput
- Per-plugin processing times
- Flow control queue depth (when enabled)
- Endpoint scoring distributions

OpenTelemetry distributed tracing is also supported for detailed request-level visibility.

## Configuration

The EPP is configured through the `EndpointPickerConfig` resource, which defines the plugin pipeline and scheduling profiles. The configuration is typically provided as a YAML file referenced from the InferencePool Helm values.

### Configuration Structure

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
# Optional feature gates
featureGates:
  - "flowControl"
  - "prepareDataPlugins"
# Plugin definitions
plugins:
  - type: <plugin-type>         # Required: the plugin implementation
    name: <unique-name>         # Optional: override name (needed for multiple instances of same type)
    parameters:                 # Optional: plugin-specific configuration
      key: value
# Scheduling profiles
schedulingProfiles:
  - name: <profile-name>       # Profile name (e.g., "default", "prefill", "decode")
    plugins:
      - pluginRef: <plugin-name>  # References a plugin defined above
        weight: <float>           # Scorer weight (ignored for filters and pickers)
```

The `plugins` section registers all plugins and their parameters. The `schedulingProfiles` section defines one or more named profiles, each specifying which plugins to run and (for scorers) what weight to assign.

### Helm Values

The EPP is deployed alongside the InferencePool via the upstream Helm chart. Key Helm values:

| Field | Description | Example |
|---|---|---|
| `inferenceExtension.replicas` | Number of EPP replicas | `1` |
| `inferenceExtension.image` | Container image for the EPP | `ghcr.io/llm-d/llm-d-inference-scheduler:v0.7.0` |
| `inferenceExtension.extProcPort` | Port the EPP listens on for ext-proc traffic | `9002` |
| `inferenceExtension.pluginsConfigFile` | Filename for the scheduling plugin configuration | `"custom-plugins.yaml"` |
| `inferenceExtension.pluginsCustomConfig` | Inline scheduling plugin configuration | See examples below |
| `inferenceExtension.tracing.enabled` | Enable OpenTelemetry distributed tracing | `false` |
| `inferenceExtension.tracing.otelExporterEndpoint` | OpenTelemetry collector endpoint | `"http://otel-collector:4317"` |
| `inferenceExtension.monitoring.prometheus.enabled` | Enable Prometheus metrics scraping | `true` |
| `inferenceExtension.monitoring.interval` | Prometheus scrape interval | `"10s"` |

### Enabling Flow Control

Enable flow control by adding the `flowControl` feature gate:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
featureGates:
  - "flowControl"
```

See the [flow control configuration guide](https://gateway-api-inference-extension.sigs.k8s.io/guides/flow-control/) for tuning saturation thresholds.

