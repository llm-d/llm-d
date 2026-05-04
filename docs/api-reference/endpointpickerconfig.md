# EndpointPickerConfig (EPP Configuration)

`EndpointPickerConfig` defines the internal configuration for the **Endpoint Picker (EPP)**. Unlike Kubernetes resources (like `InferencePool`), this is a configuration schema used to initialize the EPP binary, typically provided via a ConfigMap or a local file.

**Version:** `config.apix.gateway-api-inference-extension.sigs.k8s.io/v1alpha1`

---

## EndpointPickerConfig

| Field | Description |
| --- | --- |
| `featureGates` | `[]string` <br> A set of flags to enable experimental features (e.g., `flowControl`). |
| `plugins` | [][PluginSpec](#pluginspec) <br> **Required** <br> List of plugins to be instantiated (e.g., scorers, adapters, reporters). |
| `schedulingProfiles` | [][SchedulingProfile](#schedulingprofile) <br> **Required** <br> Named profiles that group plugins into routing slots. |
| `saturationDetector` | [SaturationDetectorConfig](#saturationdetectorconfig) <br> Configuration for the saturation detector plugin. Defaults to `utilization-detector`. |
| `dataLayer` | [DataLayerConfig](#datalayerconfig) <br> Configures the DataLayer for metadata extraction and processing. |
| `flowControl` | [FlowControlConfig](#flowcontrolconfig) <br> Configures global and per-priority admission control. Only respected if the `flowControl` feature gate is enabled. |
| `parser` | [ParserConfig](#parserconfig) <br> Specifies the parsing logic for protocol messages (e.g., `openai-parser`). |

## PluginSpec

Defines a plugin instance and its parameters.

| Field | Description |
| --- | --- |
| `name` | `string` <br> Unique name for this plugin instance. If omitted, `type` is used. |
| `type` | `string` <br> **Required** <br> The plugin type to instantiate (e.g., `least-request`, `openai-parser`). |
| `parameters` | `json.RawMessage` <br> Arbitrary parameters passed to the plugin's factory function. |

## SchedulingProfile

Groups plugins to define specific routing behavior.

| Field | Description |
| --- | --- |
| `name` | `string` <br> **Required** <br> Name of the profile. |
| `plugins` | [][SchedulingPlugin](#schedulingplugin) <br> **Required** <br> List of plugins associated with this profile. |

## SchedulingPlugin

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br> **Required** <br> Reference to a named plugin in the top-level `plugins` list. |
| `weight` | `float64` <br> Weight used if the plugin is a Scorer. |

## FlowControlConfig

Configures admission control and queuing.

| Field | Description |
| --- | --- |
| `maxBytes` | `resource.Quantity` <br> Global maximum aggregate byte size of all active requests. |
| `maxRequests` | `resource.Quantity` <br> Global maximum number of concurrent requests. |
| `defaultRequestTTL` | `duration` <br> Fallback timeout for queued requests. |
| `defaultPriorityBand` | [PriorityBandConfig](#prioritybandconfig) <br> Template for priority levels not explicitly configured. |
| `priorityBands` | [][PriorityBandConfig](#prioritybandconfig) <br> Explicit policies for specific priority levels. |
| `usageLimitPolicyPluginRef` | `string` <br> Reference to a `UsageLimitPolicy` plugin for adaptive capacity management. |

## PriorityBandConfig

| Field | Description |
| --- | --- |
| `priority` | `int` <br> Integer priority level. Higher is more critical. |
| `maxBytes` | `resource.Quantity` <br> Max bytes allowed for this priority band. |
| `maxRequests` | `resource.Quantity` <br> Max concurrent requests allowed for this band. |
| `fairnessPolicyRef` | `string` <br> Policy governing flow selection (default: `global-strict-fairness-policy`). |
| `orderingPolicyRef` | `string` <br> Policy governing request selection within a flow (default: `fcfs-ordering-policy`). |

## DataLayerConfig

| Field | Description |
| --- | --- |
| `sources` | [][DataLayerSource](#datalayersource) <br> **Required** <br> List of metadata sources. |

## DataLayerSource

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br> **Required** <br> Reference to a plugin providing the data source. |
| `extractors` | [][DataLayerExtractor](#datalayerextractor) <br> **Required** <br> Plugins that extract specific attributes from the source. |

## SaturationDetectorConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br> Reference to a plugin instance for saturation detection. |

## ParserConfig

| Field | Description |
| --- | --- |
| `pluginRef` | `string` <br> **Required** <br> Reference to a parser plugin (default: `openai-parser`). |
