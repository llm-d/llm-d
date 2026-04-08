# Endpoint Picker (EPP)

The Endpoint Picker (EPP) is the core scheduling component of llm-d that makes LLM-aware routing decisions for inference requests.

## Functionality

The EPP is the "brains" of an llm-d deployment. It is an extensible, plugin-based component that decides which model server pod in an `InferencePool` should handle each incoming inference request.

Unlike traditional load balancers that route based on connection counts or round-robin, the EPP understands the internal state of LLM inference engines -- KV-cache utilization, prefix cache locality, request queue depth, and active request counts -- to make scheduling decisions that dramatically improve latency and throughput.

The EPP integrates with the proxy layer via Envoy's [External Processing (ext-proc)](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) protocol. When a request arrives at the proxy, the proxy calls the EPP to select a backend endpoint, and the EPP returns the optimal pod address.

## Design

### Lifecycle of a Request

The following diagram shows the end-to-end lifecycle of a request as it flows through the EPP plugin pipeline:

```
                          ┌──────────────────────────────────────────────┐
                          │              EndpointPickerConfig            │
                          │                                              │
  ┌─────────┐    ext-proc │  ┌───────────┐                               │
  │  Proxy  │────────────►│  │ Handlers  │  Prepare request context      │
  │(Gateway)│             │  │(tokenizer,│  (tokenize, set profile, etc) │
  │         │             │  │ profiles) │                               │
  │         │             │  └─────┬─────┘                               │
  │         │             │        │                                      │
  │         │             │        ▼                                      │
  │         │             │  ┌───────────┐  Remove ineligible endpoints  │
  │         │             │  │  Filters  │  (e.g. prefill-only,          │
  │         │             │  │           │   decode-only)                │
  │         │             │  └─────┬─────┘                               │
  │         │             │        │                                      │
  │         │             │        ▼                                      │
  │         │             │  ┌───────────┐  Score remaining endpoints    │
  │         │             │  │  Scorers  │  (cache locality, queue       │
  │         │             │  │ (weighted)│   depth, utilization, etc)    │
  │         │             │  └─────┬─────┘                               │
  │         │             │        │                                      │
  │         │             │        ▼                                      │
  │         │             │  ┌───────────┐  Select highest-scoring       │
  │         │             │  │  Picker   │  endpoint                     │
  │         │◄────────────│  │           │                               │
  │         │  endpoint   │  └───────────┘                               │
  └────┬────┘             └──────────────────────────────────────────────┘
       │
       │  route request
       ▼
  ┌──────────┐
  │  Model   │
  │  Server  │
  │  (vLLM)  │
  └──────────┘
```

The steps are:

1. **Request arrival** -- An inference request (e.g., an OpenAI-compatible chat completion) arrives at the proxy (Gateway).
2. **ext-proc call** -- The proxy invokes the EPP via the ext-proc protocol, passing the request headers and body.
3. **Handler plugins** -- Handler plugins prepare the request context. This may include tokenizing the prompt (for cache-aware scoring), selecting a scheduling profile (e.g., `prefill` vs. `decode` in disaggregated serving), or annotating headers.
4. **Filter plugins** -- Filter plugins narrow the set of candidate endpoints. For example, in disaggregated serving, the `prefill-filter` removes decode-only pods and vice versa.
5. **Scorer plugins** -- Each scorer plugin independently scores all remaining candidate endpoints. Scores are multiplied by configurable weights and summed to produce a final score per endpoint.
6. **Picker plugin** -- The picker selects the endpoint with the best combined score (typically `max-score-picker`).
7. **Response** -- The EPP returns the selected endpoint address to the proxy, which routes the request to that model server pod.

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

## Examples

### Standard Deployment

A standard deployment uses approximate prefix cache scoring with queue-based load balancing:

```yaml
pluginsCustomConfig:
  custom-plugins.yaml: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
      - type: prefix-cache-scorer
      - type: kv-cache-utilization-scorer
      - type: queue-scorer
      - type: max-score-picker
    schedulingProfiles:
      - name: default
        plugins:
          - pluginRef: prefix-cache-scorer
            weight: 2.0
          - pluginRef: kv-cache-utilization-scorer
            weight: 2.0
          - pluginRef: queue-scorer
            weight: 1.0
          - pluginRef: max-score-picker
```

### Prefill/Decode Disaggregation

Disaggregated serving uses separate scheduling profiles for prefill and decode phases:

```yaml
pluginsCustomConfig:
  pd-config.yaml: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    featureGates:
      - prepareDataPlugins
    plugins:
      - type: prefill-header-handler
      - type: prefix-cache-scorer
        parameters:
          maxPrefixBlocksToMatch: 256
          lruCapacityPerServer: 31250
      - type: queue-scorer
      - type: prefill-filter
      - type: decode-filter
      - type: max-score-picker
      - type: prefix-based-pd-decider
        parameters:
          nonCachedTokens: 16
      - type: pd-profile-handler
        parameters:
          primaryPort: 0
          deciderPluginName: prefix-based-pd-decider
    schedulingProfiles:
      - name: prefill
        plugins:
          - pluginRef: prefill-filter
          - pluginRef: prefix-cache-scorer
            weight: 2
          - pluginRef: queue-scorer
            weight: 1
          - pluginRef: max-score-picker
      - name: decode
        plugins:
          - pluginRef: decode-filter
          - pluginRef: prefix-cache-scorer
            weight: 2
          - pluginRef: queue-scorer
            weight: 1
          - pluginRef: max-score-picker
```

In this configuration:
- The `pd-profile-handler` uses a decider plugin (`prefix-based-pd-decider`) to determine whether a request should be routed to a prefill or decode endpoint.
- Each phase has its own scheduling profile with appropriate filters.
- Scorers are shared across profiles but can be weighted differently per profile.

### Precise Prefix Cache with KV-Events

For real-time cache awareness, deploy the EPP with a tokenizer sidecar and KV-Events subscriber:

```yaml
pluginsCustomConfig:
  precise-prefix-cache-config.yaml: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
      - type: single-profile-handler
      - type: tokenizer
        parameters:
          modelName: Qwen/Qwen3-32B
          udsTokenizerConfig:
            socketFile: /tmp/tokenizer/tokenizer-uds.socket
      - type: precise-prefix-cache-scorer
        parameters:
          tokenProcessorConfig:
            blockSize: 64
          indexerConfig:
            speculativeIndexing: true
            tokenizersPoolConfig:
              modelName: Qwen/Qwen3-32B
              local: null
              hf: null
              uds:
                socketFile: /tmp/tokenizer/tokenizer-uds.socket
          kvEventsConfig:
            topicFilter: "kv@"
            concurrency: 4
            discoverPods: false
            zmqEndpoint: "tcp://*:5557"
      - type: kv-cache-utilization-scorer
      - type: queue-scorer
      - type: max-score-picker
    schedulingProfiles:
      - name: default
        plugins:
          - pluginRef: precise-prefix-cache-scorer
            weight: 3.0
          - pluginRef: kv-cache-utilization-scorer
            weight: 2.0
          - pluginRef: queue-scorer
            weight: 2.0
          - pluginRef: max-score-picker
```

This configuration requires additional deployment settings for the tokenizer sidecar and ZMQ ports. See the [precise prefix cache guide](../../../guides/precise-prefix-cache-aware/) for the full deployment setup.

### Tiered Prefix Cache (GPU + CPU)

Use multiple prefix cache scorer instances for tiered memory:

```yaml
schedulingProfiles:
  - name: default
    plugins:
      - pluginRef: gpu-prefix-cache-scorer
        weight: 1.0
      - pluginRef: cpu-prefix-cache-scorer
        weight: 1.0
      - pluginRef: kv-cache-utilization-scorer
        weight: 2.0
      - pluginRef: queue-scorer
        weight: 2.0
```
