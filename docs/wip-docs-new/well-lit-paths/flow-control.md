# Flow Control

Flow control multiplexing of different request classes onto the same model deployment.

LLM inference latency curves are non-linear with intense saturation dynamics -- once a server crosses a utilization threshold, latency spikes sharply and quality of service collapses for all requests. In multi-tenant environments, a single client sending a burst of requests can starve every other tenant. Without admission control, burst traffic causes queue buildup, noisy-neighbor effects, and cascading KV-cache evictions that degrade all subsequent requests.

Flow control adds priority-based admission control to the EPP. Requests are classified into priority bands -- **critical** requests (e.g., realtime clients) are always honored first with SLO objectives (TTFT, TPOT, NTPOT) applied even under load. **Sheddable** requests (e.g., batch jobs) are treated with lower priority, may be delayed or shed to preserve SLOs for critical requests, and are optimized only when capacity permits. When the EPP detects saturation, it kills sheddable requests to protect critical traffic.

## Architecture

![Flow Control](./images/flow-control.svg)

Flow control is configured in the EPP's `EndpointPickerConfig` -- no separate deployment required. The EPP evaluates each incoming request's priority band and applies admission decisions based on real-time saturation detection. The `saturationDetector` monitors KV-cache utilization or request concurrency against a configurable threshold (default: 0.85). Priority bands are configured with `maxQueueSize` limits, and a `flowIdentifier` header (e.g., `x-client-id`) enables per-tenant fairness enforcement.

Sheddable requests flow through a **queue** that retries as capacity becomes available. When saturation clears, queued requests are dispatched in priority order. This also enables **scale-to-zero**: when no pods are running and a request arrives, the queue holds it while the autoscaler provisions new pods (2-7 minutes for model loading) rather than returning a 5xx error.

## Guide

See the [Flow Control guide](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling) for configuration within the Intelligent Inference Scheduling deployment.
