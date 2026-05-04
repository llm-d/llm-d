# EPP Configuration

The `EndpointPickerConfig` is the central configuration for the Endpoint Picker (EPP), defining the graph of plugins and parameters that drive request handling, flow control, and scheduling decisions.

The configuration text has the following form:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- ....
- ....
featureGates:
  ...
parser:
  ...
flowControl:
  ...
saturationDetector:
  ...
schedulingProfiles:
- ....
- ....
dataLayer:
  ...
```

> [!IMPORTANT]
> While the configuration syntax looks like a Kubernetes Custom Resource, it is **not** a Kubernetes CRD. The configuration is not reconciled by a controller and is only read on startup. Updating the configuration requires a restart of the EPP.

- **Metadata**: The first two lines of the configuration are constant (`apiVersion` and `kind`) and must appear as is.
- **Plugins**: Defines the set of plugins that will be instantiated and their parameters.
- **Feature Gates**: Enables or disables specific experimental or optional features (such as Flow Control).
- **Request Handling**: Manages the full lifecycle of requests around the scheduling phase, spanning protocol parsing, state preparation via data producers, and final admission decisions.
- **Flow Control**: Manages pool defense and multi-tenancy by queuing requests at the gateway to enforce priority and fairness, while evaluating pool saturation to prevent overload (combines `flowControl` and `saturationDetector` fields).
- **Scheduling**: Defines the profiles and plugins used to select the optimal model server candidate for each request (via Filter -> Score -> Pick lifecycle).
- **Data Layer**: Configures the backend sources and metrics collection used for smart scheduling decisions and observability.

## Configuration Mental Model: Plugins and Wiring

The `EndpointPickerConfig` forms a configuration **graph** that defines how the EPP operates across three layers:

- **Plugins (The Nodes)**: In the `plugins` section, you instantiate specific implementations (e.g., a custom scorer or a fairness policy) and provide their parameters.
- **Wiring (The Edges)**: In structural sections like `schedulingProfiles` or `flowControl`, you link these plugins by name to specific architectural roles (e.g., telling a profile to use a specific scorer).
- **Static Runtime Configuration**: Alongside the graph, flat configuration parameters (like `maxBytes` or `defaultRequestTTL`) set static operational limits and defaults for the runtime.

This design allows you to define a plugin once and reuse it across multiple profiles or priority bands without duplicating its parameters.

> [!NOTE]
> **Auto-Wiring**: Some subsystems support automatic binding. If a plugin is declared in the top-level `plugins` list and implements a specific Go interface (like `Admitter`, `DataProducer`, or advanced hooks like `PreRequest`, `ResponseHeaderProcessor`, and `ResponseBodyProcessor`), the system will automatically discover and bind it to its role without requiring an explicit edge in the structural configuration.

To ensure the integrity of this graph, the following **validation rules** apply across all layers:
- **Valid References**: Any field that references a plugin (e.g., `pluginRef` in `schedulingProfiles` or `saturationDetector`) must reference a valid name defined in the top-level `plugins` section.
- **Unique Names**: All instances within lists that require naming (like `schedulingProfiles`) must have unique, non-empty names.
- **Data Dependencies**: The system validates that metrics extractors form a Directed Acyclic Graph (DAG) without circular dependencies, ensuring correct execution order.

## Using the `EndpointPickerConfig`

Use the `--config-file` command-line argument to specify the path to the configuration file. For example:

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
        - "/etc/epp/epp-config.yaml" # Typically mounted from a ConfigMap
```

If the configuration is passed as inline text, use the `--config-text` command-line argument. For example:

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
          - type: approx-prefix-cache-producer
            parameters:
              blockSizeTokens: 5
              maxPrefixBlocksToMatch: 256
              lruCapacityPerServer: 31250
          schedulingProfiles:
          - name: default
            plugins:
            - pluginRef: prefix-cache-scorer
              weight: 1 # Default
```

## Configuration Guide

### `plugins`

This section declares the set of plugins to be instantiated along with their parameters.

Each plugin can also be given a name, enabling the same plugin type to be instantiated multiple times, if needed (such as when configuring multiple scheduling profiles). Each entry in this section has the following form:

```yaml
- name: aName
  type: a-type
  parameters:
    param1: val1
    param2: val2
```

The fields in a plugin entry are:

- `name` which is optional, provides a name by which the plugin instance can be referenced. If this field is omitted, the plugin's type will be used as its name.
- `type` specifies the type of the plugin to be instantiated.
- `parameters` which is optional, defines the set of parameters used to configure the plugin in question. The actual set of parameters varies from plugin to plugin.

### `featureGates`

The `featureGates` section enables optional or experimental features in the EPP. Features listed here are activated; if omitted, they remain disabled.

```yaml
featureGates:
- flowControl
```

**Supported Feature Gates:**

- `flowControl`: Enables the Admission and Flow Control layer. This must be enabled to use the `flowControl` configuration section.

### Request Handling

This section covers components that process requests and responses before they reach the scheduling phase, or after a backend has been selected.

> For full architectural details and a list of available parsers, admitters, and data producers, see the [Request Handling reference](request-handling.md).

#### Parsers

The `parser` section configures how the EPP understands protocol messages (e.g., OpenAI or vLLM payloads). To use a non-default parser, you must first instantiate it in the `plugins` section and then reference its name in the `parser` field:

```yaml
plugins:
- name: myParser
  type: vllmgrpc-parser
# ...
parser:
  pluginRef: myParser
```

If unspecified, `openai-parser` is used by default.

#### Admitters & Data Producers

Admitters and Data Producers are specialized plugins that execute during the initial request processing phase:
*   **Admitters** perform early checks to accept or reject requests before they enter the queue.
*   **Data Producers** gather per request contextual information (like predicted latency or prefix cache status) required by downstream components.

As introduced in the [Mental Model](#configuration-mental-model-plugins-and-wiring), these plugins support automatic interface-based binding. This reduces boilerplate configuration that would otherwise be needed to wire them explicitly.

If an admitter or data producer plugin is declared in the top-level `plugins` list, the system automatically recognizes it by its capabilities at startup and binds it to the appropriate lifecycle hook:

*   **Admitters**: Automatically bound if they implement the Go interface for admitting or rejecting requests early.
*   **Data Producers**: Automatically bound if they implement the Go interface for gathering per-request data (like latency predictions) needed by other components.

To enable these plugins, simply list them in the `plugins` section:

```yaml
plugins:
- name: latency-admitter
  type: latency-slo-admitter
  parameters: ...
# Add the predicted latency data producer which does the computation of predicted latency. The predicted latency is consumed by the latency-slo-admitter.
- name: latency-producer
  type: predicted-latency-producer
  parameters: ...
```

They are automatically active and do not need to be referenced elsewhere in the configuration.



---

### Flow Control

See [Flow Control](flow-control.md) for more architectural details on how the EPP's flow control layer works internally.

The `flowControl` section configures the EPP's Flow Control layer, which acts as a pool defense mechanism by buffering requests before they reach backend model servers. Flow Control implements a 3-tier dispatch hierarchy: **Priority → Fairness → Ordering**. For a visual breakdown of how this looks in practice, see the [Queuing Topology diagram in the Flow Control reference](flow-control.md#queuing-topology--the-3-tier-dispatch).

When flow control is enabled (via the `FlowControl` feature gate), incoming requests are queued in memory and dispatched according to configured priority bands, fairness policies, and ordering policies. When the pool is saturated (as determined by the [saturation detector](#saturationdetector)), requests are held in the queue until capacity frees up.

The following example demonstrates a complete `EndpointPickerConfig` with flow control enabled, showing how to configure the `featureGates`, `plugins`, `saturationDetector`, and `flowControl` sections to work together.

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig

featureGates:
- flowControl

plugins:
- type: round-robin-fairness-policy
- type: fcfs-ordering-policy
- type: global-strict-fairness-policy
- type: utilization-detector
# ... other plugins ...

saturationDetector:
  pluginRef: utilization-detector # Default

flowControl:
  maxBytes: 0 # Default: unlimited
  maxRequests: 0 # Default: unlimited
  defaultRequestTTL: "0s" # Default: uses client context deadline

  defaultPriorityBand:
    maxBytes: "1Gi" # Default
    maxRequests: 0 # Default: unlimited
    orderingPolicyRef: fcfs-ordering-policy # Default
    fairnessPolicyRef: global-strict-fairness-policy # Default

  priorityBands: # Only showing overrides; fields not specified inherit from defaults
  - priority: 100
    maxBytes: "5Gi"
    maxRequests: 500
    fairnessPolicyRef: round-robin-fairness-policy

  - priority: 50
    maxBytes: "2Gi"
    maxRequests: 200

# ... other sections (schedulingProfiles, dataLayer, etc.) ...
```

##### Global Fields

- `maxBytes`: Global capacity limit across all priority levels. Supports Kubernetes resource quantity format (e.g., `10Gi`, `512Mi`) or plain integers (bytes). If `0` or omitted, no global limit is enforced (unlimited).
- `maxRequests`: Optional global maximum request count limit. If `0` or omitted, no global limit is enforced (unlimited).
- `defaultRequestTTL`: Fallback timeout for requests that do not carry a deadline. If `0` or omitted, it defaults to the client context deadline (which may wait indefinitely).
- `defaultPriorityBand`: A template used to dynamically provision priority bands that are not explicitly configured in `priorityBands`.
- `priorityBands`: A list of explicit configurations for specific priority levels.

##### Priority Band Fields

These fields apply to both `defaultPriorityBand` and entries in `priorityBands`:

- `priority`: (Required for `priorityBands` entries) Integer priority level; higher values mean higher priority.
- `maxBytes`: Aggregate byte limit for the band. Default: `1Gi`.
- `maxRequests`: Concurrent request limit for the band. Default: no per-band limit.
- `orderingPolicyRef`: References a plugin name for request ordering within the band. Default: `fcfs-ordering-policy`.
- `fairnessPolicyRef`: References a plugin name for fairness policy within the band. Default: `global-strict-fairness-policy`.

For a full list of available Fairness and Ordering policies, see the [Flow Control reference](flow-control.md#concrete-plugins).

##### Saturation Detector

> [!NOTE]
> While `saturationDetector` is presented here conceptually as part of Flow Control, it is a **top-level field** in the YAML schema, at the same level as `flowControl`.

The `saturationDetector` section configures the mechanism that evaluates whether the backend InferencePool is overloaded.

The `saturationDetector` section has the following form:

```yaml
saturationDetector:
  pluginRef: utilization-detector # Default
```

###### Fields

- `pluginRef`: References a plugin instance defined in the global `plugins` section. Defaults to `utilization-detector` if omitted or empty. *Note: If a `utilization-detector` is not explicitly defined in your `plugins` array, the gateway will automatically instantiate one under the hood using standard default parameters.*

For a full list of available Saturation Detector plugins, see the [Flow Control reference](flow-control.md#concrete-plugins).

---

### Scheduling Profiles

The `schedulingProfiles` section configures the EPP's Scheduling component. For full architectural details and a list of available filters, scorers, and pickers, see the [Scheduling reference](scheduling.md).

Incoming requests are routed to candidate model servers by executing a pipeline of filters, scorers, and a final picker defined in these profiles.

The following example demonstrates how to configure a scheduling profile with concrete values that are recommended for a typical production setup:

```yaml
schedulingProfiles:
- name: default
  plugins:
  - pluginRef: label-selector-filter # Optional: not in default profile
  - pluginRef: precise-prefix-cache-scorer # Recommended: not in default profile
    weight: 3.0
  - pluginRef: kv-cache-utilization-scorer # Recommended: not in default profile
    weight: 2.0
  - pluginRef: queue-scorer # Recommended: not in default profile
    weight: 2.0
  - pluginRef: max-score-picker # Default picker (auto-injected if omitted)
```

##### Scheduling Profile Fields

- `name`: The unique name of the scheduling profile.
- `plugins`: A list of plugins that make up the scheduling pipeline for this profile.

##### Profile Plugin Fields

- `pluginRef`: References a plugin by its name (or type if name was omitted) defined in the top-level `plugins` section.
- `weight`: Optional float weight applied if the referenced plugin is a Scorer. If omitted for a scorer, it defaults to `1.0`.

> [!CAUTION]
> If you define multiple pickers in the top-level `plugins` section and omit `schedulingProfiles`, the auto-generated `default` profile will include references to **all** of them, which will cause an error during initialization (see Multiple Pickers below).

<details>
<summary>Defaulting Behaviors</summary>

The system applies a multi-tiered defaulting logic for scheduling profiles:

- **Tier 1: Omitted `schedulingProfiles`**: If the `schedulingProfiles` section is entirely omitted, a profile named `default` is automatically created. This profile will reference **all Filter, Scorer, and Picker plugins** defined in the top-level `plugins` section.
- **Tier 2: Empty `plugins` in a profile**: If you define a profile but leave the `plugins` list empty, it is valid but only gets the auto-injected picker (see Tier 3).
- **Tier 3: Missing Picker in a profile**: If a profile does not reference a picker plugin, the system automatically injects `max-score-picker`.

</details>

##### Profile Execution Rules

While the YAML configuration presents a flat list of plugins within a profile, the framework processes them with specific rules:

- **Interface Roles**: Internally, the framework categorizes referenced plugins by their role (Filter, Scorer, or Picker) based on the interfaces they implement.
- **Execution Order**: Plugins are executed in this order: Filters first, then Scorers, and finally the Picker.
- **Multiple Pickers**: A scheduling profile **cannot** have more than one picker. Referencing more than one picker in a profile's `plugins` list will cause a runtime error during profile initialization.
- **Scorer Weights**: If the `weight` field is omitted for a scorer, it defaults to `1.0`. Scores from multiple scorers are accumulated after multiplying by their respective weights.

##### Profile Handlers and Use Cases

- **Multiple Profiles**: While a single profile is sufficient for simple serving, advanced use cases like **disaggregated prefill** require two or more profiles to handle different types of requests differently.
- **Profile Handler**: When multiple profiles are defined, you must instantiate and configure a **Profile Handler** plugin in the top-level `plugins` section. The Profile Handler determines which `SchedulingProfile` to use for each incoming request.
- **Single Profile Default**: If only one profile is defined, the system implicitly uses a `SingleProfileHandler` to route all requests to that profile, so no explicit handler configuration is required.

For a popular plugin like `prefix-cache-scorer`, you configure it in the top-level `plugins` section and reference it in a profile:

```yaml
plugins:
- type: prefix-cache-scorer
# Also add the approx-prefix-cache-producer (data producer) when passing parameters to the prefix cache scorer.
- type: approx-prefix-cache-producer
  parameters:
    blockSizeTokens: 64 # Default
    maxPrefixBlocksToMatch: 256 # Default
    lruCapacityPerServer: 31250 # Default

# ...

schedulingProfiles:
- name: default
  plugins:
  - pluginRef: prefix-cache-scorer
    weight: 3.0 # Default
```

<details>
<summary><b>Advanced Example: Multiple Profiles and Profile Handler</b></summary>

For advanced use cases requiring multiple profiles, you must configure a custom Profile Handler in the top-level `plugins` list. The system auto-detects it by checking which plugin implements the `ProfileHandler` interface.

```yaml
plugins:
- name: my-custom-profile-handler
  type: custom-profile-handler # Must implement framework.ProfileHandler
  parameters:
    # ... handler specific configuration ...
- name: filter-a
  type: some-filter
- name: filter-b
  type: another-filter
- name: scorer-1
  type: some-scorer
- name: max-score-picker
  type: max-score-picker

schedulingProfiles:
- name: profile-a
  plugins:
  - pluginRef: filter-a
  - pluginRef: scorer-1
  - pluginRef: max-score-picker
- name: profile-b
  plugins:
  - pluginRef: filter-b
  - pluginRef: scorer-1
  - pluginRef: max-score-picker
```

**Important:** Only one profile handler plugin is allowed in the configuration. If multiple profiles are defined, you must provide a handler that supports them (the default `single-profile-handler` does not support multiple profiles).

</details>

### `dataLayer`

The `dataLayer` section configures the backend sources and metrics collection used for smart scheduling decisions and observability. It defines a list of data sources and the extractors that pull data from them.

> For full details and a list of available data sources and extractors, see the Data Layer reference (TODO: add link to datalayer.md once written).

```yaml
dataLayer:
  sources:
  - pluginRef: metrics-data-source # References a plugin in the 'plugins' section
    extractors:
    - pluginRef: core-metrics-extractor # References a plugin in the 'plugins' section
```

##### Fields

- `sources`: A list of data sources to be polled or monitored.
  - `pluginRef`: References a plugin instance defined in the global `plugins` section that implements the `DataSource` interface.
  - `extractors`: A list of extractors associated with this data source.
    - `pluginRef`: References a plugin instance defined in the global `plugins` section that implements the `Extractor` interface.

> [!NOTE]
> If the `dataLayer` section is omitted, the system automatically instantiates default plugins (the `metrics-data-source` and `core-metrics-extractor`) to enable standard metrics collection and extraction for scheduling decisions.

## High Availability

To deploy the EndpointPicker in a high-availability (HA) active-passive configuration, set `replicas` to be greater than one. In such a setup, only one "leader" replica will be active and ready to process traffic at any given time. If the leader pod fails, another pod will be elected as the new leader, ensuring service continuity.

To enable HA, ensure that the number of replicas in the EPP Deployment is greater than 1.

## Monitoring

The EPP exposes a Prometheus-compatible metrics endpoint on **port 9090** at `/metrics`. These metrics provide visibility into request processing, scheduling decisions, flow control behavior, and backend pool health.

> For full upstream documentation, see the [Gateway API Inference Extension Metrics & Observability Guide](https://gateway-api-inference-extension.sigs.k8s.io/guides/metrics-and-observability/).

### EPP Metrics by Subsystem

Metrics are organized by the subsystem that owns the logic. For detailed tables of metrics available in each subsystem, see:

*   **[Request Handling Metrics](request-handling.md#metrics--observability)**: Request volume, latency, token usage, and success rates.
*   **[Flow Control Metrics](flow-control.md#metrics--observability)**: Queue sizes, dispatch cycles, and pool saturation.
*   **[Routing Metrics](scheduling.md#metrics--observability)**: Router performance and pool health state.



### Monitoring Stack

The recommended monitoring stack is **Prometheus + Grafana**. A pre-built Grafana dashboard is available at [`tools/dashboards/inference_gateway.json`](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/tools/dashboards/inference_gateway.json) in the upstream repository.

Pre-configured alert rules are also available upstream, covering:

- **High P99 latency** — triggers when P99 request latency exceeds 10 seconds
- **High error rate** — triggers when the error rate exceeds 5%
- **High queue size** — triggers when model server queue depth exceeds 50 requests
- **High KV cache utilization** — triggers when KV cache utilization exceeds 90%
