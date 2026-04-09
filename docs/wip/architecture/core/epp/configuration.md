NEEDS TO BE REDONE!


## EPP Configuration

The `EndpointPickerConfig` is used to cofigure the EPP deployment.

The configuration text has the following form:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- ....
- ....
schedulingProfiles:
- ....
- ....
saturationDetector:
  ...
data:
  ...
flowControl:
  ...
parser:
  ...
featureGates:
  ...
```

> NOTE: While the configuration text looks like a Kubernetes CRD, it is NOT a Kubernetes CRD. Specifically, the config is not reconciled upon, and is only read on startup. This behavior is intentional, as augmenting the scheduling config without redeploying the EPP is not supported.

- The first two lines of the configuration are constant and must appear as is.
- The [`plugins`](#plugins) section defines the set of plugins that will be instantiated and their parameters.
- The [`schedulingProfiles`](#schedulingProfiles) section defines the set of scheduling profiles that can be used in scheduling requests to pods.
- The [`saturationDetector`](#saturationDetector) section configures the saturation detector.
- The [`flowControl`](#flowControl) section configures the Flow Control layer, which manages request concurrency and fairness.
- The [`data`](#data) section configures the data layer, which is used to gather information (such as metrics) used in making scheduling decisions.
- The [`parser`](#parser) section configures the parser, which is used to understand the payload of requests and responses for features like prefix-cache aware routing and 
- The [`featureGates`](#featureGates) section allows the enablement of experimental features of the IGW. This section is described in more detail in the section Feature Gates.usage tracking.

### Using the `EndpointPickerConfig`

The `EndpointPickerConfig` command line argument `--config-file` should be used to specify the full path of the file in question. For example:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${EPP_NAME}
  ...
spec:
  ...
  template:
    ...
    spec:
      ...
      containers:
      - name: epp
        image: ghcr.io/llm-d/llm-d-inference-scheduler:latest
        imagePullPolicy: IfNotPresent
        args:
        - --pool-name
        - "${POOL_NAME}"
        ...
        - --config-file
        - "/etc/epp/epp-config.yaml"
```

If the configuration is passed as in-line text the EPP command line argument `--config-text` should be used. For example:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${EPP_NAME}
  ...
spec:
  ...
  template:
    ...
    spec:
      ...
      containers:
      - name: epp
        image: ghcr.io/llm-d/llm-d-inference-scheduler:latest
        imagePullPolicy: IfNotPresent
        args:
        - --pool-name
        - "${POOL_NAME}"
        ...
        - --config-text
        - |
          apiVersion: inference.networking.x-k8s.io/v1alpha1
          kind: EndpointPickerConfig
          plugins:
          - type: prefix-cache-scorer
            parameters:
              blockSizeTokens: 5
              maxPrefixBlocksToMatch: 256
              lruCapacityPerServer: 31250
          schedulingProfiles:
          - name: default
            plugins:
            - pluginRef: prefix-cache-scorer
              weight: 50
```

### Configuration Guide

#### `plugins`

The section declares the set of plugins to be instantiated along with their parameters.

Each plugin can also be given a name, enabling the same plugin type to be instantiated multiple times, if needed (such as when configuring multiple scheduling profiles). Each entry in this section has the following form:

```yaml
- name: aName
  type: a-type
  parameters:
    parm1: val1
    parm2: val2
```

The fields in a plugin entry are:
- `name` which is optional, provides a name by which the plugin instance can be referenced. If this field is omitted, the plugin's type will be used as its name.
- `type` specifies the type of the plugin to be instantiated.
- `parameters` which is optional, defines the set of parameters used to configure the plugin in question. The actual set of parameters varies from plugin to plugin.

#### `schedulingProfiles`

The `schedulingProfile` section defines how the EPP's Scheduling component works. If one is not defined, a default `schedulingProfile` named `default` will be added and will reference all of the instantiated plugins.

The number of scheduling profiles depends on the use case:
- For aggregated serving - one profile is needed
- For disaggregated servings - two profiles are required (one for prefill and one for decode).

Each `schedulingProfile` can have:
- a set of `filters` (optional -- if unset, uses no filter)
- a set of `scorers` with `weights`
- a `picker` (optional -- if unset, uses `max-score-picker`)

Each entry in this section has the following form:

```yaml
- name: aName
  plugins:
  - pluginRef: plugin1
  - pluginRef: plugin2
    weight: 50
```

Below is a simple concrete example, which configures the EPP to use aggregated serving, consider the prefix-cache hit, the queue depth, and kv cache utilization in the scheduling decision.

```yaml
pluginsConfigFile: "custom-plugins.yaml"
  pluginsCustomConfig:
    custom-plugins.yaml: |
      apiVersion: inference.networking.x-k8s.io/v1alpha1
      kind: EndpointPickerConfig
      plugins:
      - type: prefix-cache-scorer
      - type: queue-scorer
      - type: kv-cache-utilization-scorer
      - type: max-score-picker
      schedulingProfiles:
      - name: default
        plugins:
        - pluginRef: prefix-cache-scorer
          weight: 3
        - pluginRef: queue-scorer
          weight: 2
        - pluginRef: kv-cache-utilization-scorer
          weight: 2
        - pluginRef: max-score-picker
```

There are two types of plugins related to Scheduling: `Scorers` and `Pickers`

#### Scorers

During the scheduling process, each pod recieves a score for each scorer in the `schedulingProfile`:

- `prefix-cache-scorer`: Scores pods based on the amount of the prompt is believed to be in the pod's KvCache. Parameters:
    - `blockSize`: specified the size of the blocks to break up the input prompt when calculating the block hashes. If not specified defaults to 64 
    - `maxPrefixBlocksToMatch`: specifies the maximum number of prefix blocks to match. If not specified defaults to 256
    - `lruCapacityPerServer`: specifies the capacity of the LRU indexer in number of entries per server (pod). If not specified defaults to 31250

- `lora-affinity-scorer`: Scores pods based on whether the requested LoRA adapter is already loaded in the pod's HBM, or if the pod is ready to load the LoRA on demand. Parameters:
    - none

- `kv-cache-utilization-scorer`: Scores the candidate pods based on their KV cache utilization. Parameters:
    - none

- `queue-scorer`: Scores list of candidate pods based on the pod's waiting queue size. The lower the waiting queue size the pod has, the higher the score it will get (since it's more available to serve new request). Parameters:
    - none

- `running-requests-size-scorer`: Scores candidate pods based on the number of requests currently being processed (in-flight) on each pod. Pods with fewer running requests receive a higher score. Scores are normalized across the candidate set — the pod with the fewest running requests scores 1.0, the pod with the most scores 0.0, and all others are linearly interpolated. When all candidates have the same count, every pod receives a neutral score of 1.0.
    - none

---> XXX ---> What Else Is Missing?

#### Pickers

After each pod recieves a score for each scorer which are combined using the `weights`, the `picker` configures how we select the pod.

- `max-score-picker`: Picks the pod with the maximum score from the list of candidates. This is the default picker plugin if not specified. Parameters:
    - `maxNumOfEndpoints`: Maximum number of endpoints to pick from the list of candidates, based on the scores of those endpoints. If not specified defaults to 1

- `random-picker`: Picks a random pod from the list of candidates. Parameters:
    - `maxNumOfEndpoints`: Maximum number of endpoints to pick from the list of candidates. If not specified defaults to 1

- `weighted-random-picker`: Picks pod(s) from the list of candidates based on weighted random sampling using A-Res algorithm. Parameters:
    - `maxNumOfEndpoints`: Maximum number of endpoints to pick from the list of candidates. If not specified defaults to 1.

See [Scheduling](scheduling.md) for more architectural details on how the EPP's scheduler uses these components internally.

#### `flowControl`

The `flowControl` section configures the EPP's Flow Control layer, which acts as a pool defense mechanism by buffering requests before they reach backend model servers. Flow Control implements a 3-tier dispatch hierarchy: **Priority → Fairness → Ordering**.

When flow control is enabled (via the `FlowControl` feature gate), incoming requests are queued in memory and dispatched according to configured priority bands, fairness policies, and ordering policies. When the pool is saturated (as determined by the [saturation detector](#saturationdetector)), requests are held in the queue until capacity frees up.

The `flowControl` section has the following form:

```yaml
flowControl:
  maxBytes: 10Gi
  maxRequests: 5000
  defaultRequestTTL: 60s
  defaultPriorityBand:
    maxBytes: 1Gi
    maxRequests: 1000
    orderingPolicyRef: fcfs-ordering-policy
    fairnessPolicyRef: global-strict-fairness-policy
  priorityBands:
    - priority: 100
      maxBytes: 5Gi
      maxRequests: 500
      orderingPolicyRef: fcfs-ordering-policy
      fairnessPolicyRef: round-robin-fairness-policy
    - priority: 50
      maxBytes: 2Gi
      maxRequests: 200
```

##### Global Fields

- `maxBytes`: Global capacity limit across all priority levels. Supports Kubernetes resource quantity format (e.g., `10Gi`, `512Mi`) or plain integers (bytes). Default: unlimited.
- `maxRequests`: Optional global maximum request count limit. Default: unlimited.
- `defaultRequestTTL`: Fallback timeout for requests that do not carry a deadline. Default: uses the client context deadline (which may wait indefinitely).
- `defaultPriorityBand`: A template used to dynamically provision priority bands that are not explicitly configured in `priorityBands`.
- `priorityBands`: A list of explicit configurations for specific priority levels.

##### Priority Band Fields

These fields apply to both `defaultPriorityBand` and entries in `priorityBands`:

- `priority`: (Required for `priorityBands` entries) Integer priority level; higher values mean higher priority.
- `maxBytes`: Aggregate byte limit for the band. Default: 1 GB.
- `maxRequests`: Concurrent request limit for the band. Default: no per-band limit.
- `orderingPolicyRef`: References a plugin name for request ordering within the band. Default: `fcfs-ordering-policy`.
- `fairnessPolicyRef`: References a plugin name for fairness policy within the band. Default: `global-strict-fairness-policy`.

##### Fairness Policies

Fairness policies control how requests from different flows (e.g., different tenants) are interleaved within a priority band:

- `global-strict-fairness-policy`: Serves all requests in a single global FIFO order, with no per-flow distinction. This is the default.
- `round-robin-fairness-policy`: Cycles fairly between different flows, ensuring no single flow can starve others.

##### Ordering Policies

Ordering policies control the order in which requests are dispatched from the queue within a given flow:

- `fcfs-ordering-policy`: First-Come, First-Served ordering. This is the default.
- `edf-ordering-policy`: Earliest Deadline First — prioritizes requests closest to their deadline.
- `slo-deadline-ordering-policy`: SLO-based deadline ordering — orders requests by their SLO-derived deadlines.

Below is a concrete example that configures flow control with two priority bands, round-robin fairness for the high-priority band, and earliest-deadline-first ordering for the low-priority band:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: round-robin-fairness-policy
- type: edf-ordering-policy
flowControl:
  maxBytes: 10Gi
  defaultRequestTTL: 30s
  priorityBands:
    - priority: 100
      maxBytes: 5Gi
      maxRequests: 500
      fairnessPolicyRef: round-robin-fairness-policy
    - priority: 50
      maxBytes: 2Gi
      maxRequests: 200
      orderingPolicyRef: edf-ordering-policy
```

See [Flow Control](flow-control.md) for more architectural details on how the EPP's flow control layer works internally.

#### `saturationDetector`

The `saturationDetector` section configures the saturation detection mechanism, which acts as a safety valve to evaluate whether the backend InferencePool is overloaded and protects endpoints from exceeding optimal capacity.

The behavior of the saturation detector depends on whether flow control is enabled:

- **Flow Control enabled**: When the pool is saturated, request dispatch is paused and incoming requests are buffered in the flow control memory queues (respecting priority and fairness policies) until backend capacity frees up.
- **Flow Control disabled** (default): When the pool is saturated, "sheddable" requests (those with negative priority) are immediately rejected with HTTP 503. All other requests pass directly to the model servers.

The `saturationDetector` section has the following form:

```yaml
saturationDetector:
  pluginRef: utilization-detector
```

##### Fields

- `pluginRef`: References a plugin instance defined in the global `plugins` section. Defaults to `utilization-detector` if omitted or empty.

##### Saturation Detector Plugins

There are two available saturation detector plugins:

**`utilization-detector`** (Default)

Detects saturation based on queue depth and KV cache utilization thresholds across the pool. Parameters:

- `queueDepthThreshold` (int, default: `5`): Target queue depth limit per endpoint. When an endpoint's queue depth exceeds this value, it is considered saturated.
- `kvCacheUtilThreshold` (float64, default: `0.8`): Target KV cache utilization threshold (0.0–1.0). When an endpoint's KV cache utilization exceeds this value, it is considered saturated.
- `metricsStalenessThreshold` (duration, default: `"200ms"`): Maximum age of metrics before an endpoint is deemed stale and excluded from scheduling decisions.
- `headroom` (float64, default: `0.0`): Allowed burst capacity above thresholds before the pool is considered saturated.

**`concurrency-detector`**

Detects saturation based on in-flight request or token concurrency. Parameters:

- `concurrencyMode` (string, default: `"requests"`): Either `"requests"` (track in-flight requests) or `"tokens"` (track in-flight tokens).
- `maxConcurrency` (int64, default: `100`): Maximum in-flight requests per endpoint.
- `maxTokenConcurrency` (int64, default: `1000000`): Maximum tokens in-flight per endpoint.
- `headroom` (float64, default: `0.0`): Allowed burst capacity above the concurrency limit.

Below is a concrete example that configures the utilization detector with custom thresholds:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: utilization-detector
  parameters:
    queueDepthThreshold: 10
    kvCacheUtilThreshold: 0.9
    metricsStalenessThreshold: "500ms"
    headroom: 0.1
saturationDetector:
  pluginRef: utilization-detector
```

And an example using the concurrency detector:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: concurrency-detector
  parameters:
    concurrencyMode: "tokens"
    maxTokenConcurrency: 500000
    headroom: 0.05
saturationDetector:
  pluginRef: concurrency-detector
```

### High Availability

To deploy the EndpointPicker in a high-availability (HA) active-passive configuration set `replicas` to be greater than one. In such a setup, only one "leader" replica will be active and ready to process traffic at any given time. If the leader pod fails, another pod will be elected as the new leader, ensuring service continuity.

To enable HA, set `inferenceExtension.replicas` to a number greater than 1.

### Monitoring

The EPP exposes a Prometheus-compatible metrics endpoint on **port 9090** at `/metrics`. These metrics provide visibility into request processing, scheduling decisions, flow control behavior, and backend pool health.

> For full upstream documentation, see the [Gateway API Inference Extension Metrics & Observability Guide](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/).

#### EPP Request Metrics

The following metrics track request-level behavior. Unless otherwise noted, they carry the labels `model_name` and `target_model_name`.

| Metric | Type | Description |
|--------|------|-------------|
| `inference_objective_request_total` | Counter | Total request count per model |
| `inference_objective_request_error_total` | Counter | Total error count per model |
| `inference_objective_request_duration_seconds` | Distribution | End-to-end response latency |
| `inference_objective_normalized_time_per_output_token_seconds` | Distribution | Normalized Time Per Output Token (NTPOT) |
| `inference_objective_request_sizes` | Distribution | Request size in bytes |
| `inference_objective_response_sizes` | Distribution | Response size in bytes |
| `inference_objective_input_tokens` | Distribution | Input token count per request |
| `inference_objective_output_tokens` | Distribution | Output token count per request |
| `inference_objective_running_requests` | Gauge | Currently active requests per model |

> **Note:** Response-level metrics (response sizes, output tokens, NTPOT) require Envoy body mode to be set to `Buffered` or `Streamed`. For vLLM streaming responses with usage data, include `stream_options: {"include_usage": true}` in the request.

#### Pool & Scheduling Metrics

These metrics provide visibility into the InferencePool health and scheduling decisions.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `inference_pool_average_kv_cache_utilization` | Gauge | `name` | Average KV cache utilization across the pool |
| `inference_pool_average_queue_size` | Gauge | `name` | Average number of pending requests across the pool |
| `inference_pool_per_pod_queue_size` | Gauge | `model_server_pod`, `name` | Queue size for each individual pod |
| `inference_pool_ready_pods` | Gauge | `name` | Number of ready pods in the pool |
| `inference_extension_info` | Gauge | `commit`, `build_ref` | EPP build information |
| `inference_extension_scheduler_attempts_total` | Counter | `status`, `target_model_name`, `pod_name`, `namespace`, `port` | Number of scheduling attempts and their outcomes |

#### Flow Control Metrics

When flow control is enabled, the following metrics are exposed. They carry the labels `fairness_id`, `priority`, `outcome`, `inference_pool`, `model_name`, and `target_model_name`.

| Metric | Type | Description |
|--------|------|-------------|
| `inference_extension_flow_control_request_queue_duration_seconds` | Distribution | Time a request spends in the flow control queue |
| `inference_extension_flow_control_queue_size` | Gauge | Number of requests currently queued |
| `inference_extension_flow_control_queue_bytes` | Gauge | Total size of queued requests in bytes |
| `inference_extension_flow_control_dispatch_cycle_duration_seconds` | Distribution | Duration of each dispatch cycle |
| `inference_extension_flow_control_request_enqueue_duration_seconds` | Distribution | Time taken to enqueue a request |
| `inference_extension_flow_control_pool_saturation` | Gauge | Pool saturation level (0.0–1.0+) |

#### Monitoring Stack

The recommended monitoring stack is **Prometheus + Grafana**. A pre-built Grafana dashboard is available at [`tools/dashboards/inference_gateway.json`](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/tools/dashboards/inference_gateway.json) in the upstream repository.

Pre-configured alert rules are also available upstream, covering:

- **High P99 latency** — triggers when P99 request latency exceeds 10 seconds
- **High error rate** — triggers when the error rate exceeds 5%
- **High queue size** — triggers when queue depth exceeds 50 requests
- **High KV cache utilization** — triggers when KV cache utilization exceeds 90%
