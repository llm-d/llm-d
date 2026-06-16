# API Reference

## Core Kubernetes APIs

The following Kubernetes APIs are defined in the `inference.networking.k8s.io` (v1) and `llm-d.ai` (v1alpha2) groups.

| Resource | API Group | Version | Description |
| --- | --- | --- | --- |
| [InferencePool](inferencepool.md) | `inference.networking.k8s.io` | `v1` | Defines a pool of inference endpoints (model servers) to configure the **Endpoint Picker (EPP)** and Gateways for inference-optimized routing. |
| [InferenceObjective](inferenceobjective.md) | `llm-d.ai` | `v1alpha2` | Defines performance goals (priority, latency) for specific model workloads within a pool. |
| [InferenceModelRewrite](inferencemodelrewrite.md) | `llm-d.ai` | `v1alpha2` | Specifies rules for rewriting model names in request bodies, enabling traffic splitting and canary rollouts. |

## Component Configuration

These schemas define the internal configuration for project components and are typically provided via ConfigMaps or local files, rather than as standalone Kubernetes objects.

| Schema | API Group | Version | Description |
| --- | --- | --- | --- |
| [EndpointPickerConfig](endpointpickerconfig.md) | `llm-d.ai` | `v1alpha1` | Defines the internal configuration for the **Endpoint Picker (EPP)**, including plugins and request scheduling profiles. |

## Recognized HTTP Headers

* [EPP HTTP Headers Reference](epp-http-headers.md): The EPP inspects specific HTTP headers to manage flow control and observability for inference requests.

## Supported Request APIs

* [EPP HTTP APIs Reference](epp-http-apis.md): HTTP APIs such as OpenAI's Chat, Anthropic's Message and vLLM's Generate APIs.
* [EPP gRPC APIs Reference](epp-grpc-apis.md): gRPC APIs such as vLLM's gRPC Generate API.

## See Also

* [Glossary](glossary.md): Definitions of key terms and concepts used across this project.
