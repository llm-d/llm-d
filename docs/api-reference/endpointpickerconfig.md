# EndpointPickerConfig (EPP Configuration)

`EndpointPickerConfig` defines the internal configuration for the **Endpoint Picker (EPP)**. Unlike Kubernetes resources (like `InferencePool`), this is a configuration schema used to initialize the EPP binary, typically provided via a ConfigMap or a local file.

**Group:** `llm-d.ai`
**Version:** `v1`

> [!NOTE]
> `llm-d.ai/v1alpha1` is still accepted but deprecated; the EPP converts it to `v1` at startup and logs a deprecation warning. See [Configuration](../architecture/core/router/epp/configuration.md) for the field migration.

---

**Required** marks fields the EPP rejects a configuration without. The Go API types also annotate `plugins` and `schedulingProfiles` as required, but the loader accepts a configuration without them and supplies defaults.

## EndpointPickerConfig

| Field | Description |
| --- | --- |
| `featureGates` | `[]string` <br/> A set of flags to enable experimental features (e.g., `flowControl`). |
| `plugins` | [][PluginSpec](#pluginspec) <br/> List of plugins to be instantiated (e.g., scorers, adapters, reporters). A plugin must be listed here to be referenced from a profile. Default plugins (picker, profile handler, parsers, saturation detector) are injected when absent. |
| `schedulingProfiles` | [][SchedulingProfile](#schedulingprofile) <br/> Named profiles that group plugins into routing slots. If omitted, a single `default` profile is created with all listed filters, scorers, and pickers. |
| `dataLayer` | [DataLayerConfig](#datalayerconfig) <br/> Configures the DataLayer for metadata extraction and processing. |
| `flowControl` | [FlowControlConfig](#flowcontrolconfig) <br/> Configures global and per-priority admission control. Only respected if the `flowControl` feature gate is enabled. |
| `requestHandler` | [RequestHandlerConfig](#requesthandlerconfig) <br/> Specifies the handling logic used by the EPP to process incoming requests. |

## PluginSpec

Defines a plugin instance and its parameters.

| Field | Description |
| --- | --- |
| `name` | `string` <br/> Unique name for this plugin instance. If omitted, `type` is used. |
| `type` | `string` <br/> **Required** <br/> The plugin type to instantiate (e.g., `max-score-picker`, `openai-parser`). |
| `parameters` | `json.RawMessage` <br/> Arbitrary parameters passed to the plugin's factory function. |

## SchedulingProfile

Groups plugins to define specific routing behavior.

| Field | Description |
| --- | --- |
| `name` | `string` <br/> **Required** <br/> Name of the profile. |
| `plugins` | [][SchedulingPlugin](#schedulingplugin) <br/> List of plugins associated with this profile. A `max-score-picker` is appended if the profile has no picker. |

## SchedulingPlugin

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a named plugin in the top-level `plugins` list. |
| `weight` | `float64` <br/> Weight used if the plugin is a Scorer. |

## FlowControlConfig

Configures admission control and queuing.

| Field | Description |
| --- | --- |
| `maxBytes` | `resource.Quantity` <br/> Global maximum aggregate byte size of all active requests. |
| `maxRequests` | `resource.Quantity` <br/> Global maximum number of concurrent requests. |
| `defaultRequestTTL` | `duration` <br/> Fallback timeout for queued requests. |
| `defaultPriorityBand` | [PriorityBandConfig](#prioritybandconfig) <br/> Template for priority levels not explicitly configured. |
| `priorityBands` | [][PriorityBandConfig](#prioritybandconfig) <br/> Explicit policies for specific priority levels. |
| `usageLimitPolicyPluginRef` | `string` <br/> Reference to a `UsageLimitPolicy` plugin for adaptive capacity management. |
| `saturationDetector` | [SaturationDetectorConfig](#saturationdetectorconfig) <br/> Specifies which saturation detector plugin to use. Defaults to `utilization-detector`. |

## PriorityBandConfig

| Field | Description |
| --- | --- |
| `priority` | `int` <br/> Integer priority level. Higher is more critical. |
| `maxBytes` | `resource.Quantity` <br/> Max bytes allowed for this priority band. |
| `maxRequests` | `resource.Quantity` <br/> Max concurrent requests allowed for this band. |
| `fairnessPolicyRef` | `string` <br/> Policy governing flow selection (default: `global-strict-fairness-policy`). |
| `orderingPolicyRef` | `string` <br/> Policy governing request selection within a flow (default: `fcfs-ordering-policy`). |

## RequestHandlerConfig

Configures request handling behavior.

| Field | Description |
| --- | --- |
| `parsers` | [][ParserConfig](#parserconfig) <br/> List of parsing plugins used to process protocol messages. If unspecified, `openai-parser`, `anthropic-parser`, and `vllmhttp-parser` are configured by default. |

## DataLayerConfig

| Field | Description |
| --- | --- |
| `injectDefaults` | `bool` <br/> Controls automatic injection of the default metrics source and extractor. Defaults to `true`. Set to `false` to disable all default source injection. |
| `sources` | [][DataLayerSource](#datalayersource) <br/> List of metadata sources. |
| `discovery` | [DiscoveryConfig](#discoveryconfig) <br/> Endpoint and peer discovery plugins. If omitted, the EPP uses Kubernetes-based endpoint discovery and disables peer discovery. |
| `crossReplica` | [CrossReplicaConfig](#crossreplicaconfig) <br/> Publishes local per-endpoint state to other EPP replicas and reads theirs back. If omitted, no cross-replica syncer is used and plugins fall back to local data. |

## DiscoveryConfig

| Field | Description |
| --- | --- |
| `endpoints` | [EndpointDiscoveryConfig](#endpointdiscoveryconfig) <br/> The EndpointDiscovery plugin used to populate the endpoint datastore. When set, the EPP bypasses the Kubernetes reconcilers and relies on the plugin to enumerate endpoints, enabling operation without a Kubernetes cluster. |
| `peers` | [PeerDiscoveryConfig](#peerdiscoveryconfig) <br/> The PeerDiscovery plugin used to discover peer EPP replicas. If omitted, peer discovery is disabled. |

## EndpointDiscoveryConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a plugin instance that implements EndpointDiscovery. |

## PeerDiscoveryConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a plugin instance that implements PeerDiscovery. |

## CrossReplicaConfig

| Field | Description |
| --- | --- |
| `syncerPluginRef` | `string` <br/> Reference to the plugin instance used as the cross-replica syncer. |
| `syncInterval` | `duration` <br/> Cadence at which each replica publishes its local per-endpoint state, independent of the datalayer polling interval. A default is used if omitted. |
| `publishTimeout` | `duration` <br/> Bound on one endpoint publish, including all concurrent contributor writes. A default is used if omitted. |

## DataLayerSource

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a plugin providing the data source. |
| `extractors` | [][DataLayerExtractor](#datalayerextractor) <br/> **Required** <br/> Plugins that extract specific attributes from the source. |

## DataLayerExtractor

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a plugin that extracts attributes from the source. |

## SaturationDetectorConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> Reference to a plugin instance for saturation detection. |

## ParserConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br/> **Required** <br/> Reference to a parser plugin instance. |
