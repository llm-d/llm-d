# API Reference

## Core Kubernetes APIs

The following Kubernetes APIs are defined in the `inference.networking.k8s.io` (v1) and `inference.networking.x-k8s.io` (v1alpha2) groups.

| Resource | Version | Description |
| --- | --- | --- |
| [InferencePool](inferencepool.md) | `v1` | Defines a pool of inference endpoints (model servers) to configure the **Endpoint Picker (EPP)** and Gateways for inference-optimized routing. |
| [InferenceObjective](inferenceobjective.md) | `v1alpha2` | Defines performance goals (priority, latency) for specific model workloads within a pool. |
| [InferenceModelRewrite](inferencemodelrewrite.md) | `v1alpha2` | Specifies rules for rewriting model names in request bodies, enabling traffic splitting and canary rollouts. |

## Component Configuration

These schemas define the internal configuration for project components and are typically provided via ConfigMaps or local files, rather than as standalone Kubernetes objects.

| Schema | Version | Description |
| --- | --- | --- |
| [EndpointPickerConfig](endpointpickerconfig.md) | `v1alpha1` | Defines the internal configuration for the **Endpoint Picker (EPP)**, including plugins and request scheduling profiles. |

## Recognized HTTP Headers

*   [EPP HTTP Headers Reference](epp-http-headers.md): The EPP inspects specific HTTP headers to manage flow control and observability for inference requests.

## See Also

*   [Glossary](glossary.md): Definitions of key terms and concepts used across this project.
