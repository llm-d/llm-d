# EPP HTTP Headers Reference

This document describes the HTTP headers that the [Endpoint Picker (EPP)](../architecture/core/router/epp) inspects to manage and control inference requests, specifically for flow control, performance management, and request classification.

## Request Classification and Flow Control

These headers allow the EPP to identify the request's goals, group them for fair resource allocation, and handle model-specific targeting.

| Header | Description |
| --- | --- |
| `x-gateway-inference-objective` | Specifies the name of the `InferenceObjective` resource associated with the request. The EPP uses this to look up the corresponding objective resource in the **same namespace as the InferencePool** to apply the defined priority and performance goals. |
| `x-gateway-inference-fairness-id` | Provides a unique identifier for grouping requests for fairness-based flow control. Requests with the same ID share capacity according to the fairness policy. If omitted, the EPP defaults to `default-flow`. |
| `x-gateway-model-name-rewrite` | Specifies the target model name to be used for the request. This is an alternative approach to model name rewriting; while the `InferenceModelRewrite` API provides rule-based rewriting on the server side, this header allows for an explicit, per-request override. When present, the EPP uses this value to override the model name in the request body and for recording model-specific metrics. |

## Service Level Objectives (SLOs)

These headers are used by admission control and load balancing plugins to make decisions based on latency targets.

| Header | Description |
| --- | --- |
| `x-slo-ttft-ms` | Specifies the target Time To First Token (TTFT) in milliseconds. Used by plugins to determine if a request can be admitted while meeting the latency goal. |
| `x-slo-tpot-ms` | Specifies the target Time Per Output Token (TPOT) in milliseconds. Used for admission control based on predicted or observed token generation latency. |

---

## Implementation Notes

- **Case Sensitivity:** All header lookups are case-insensitive.
- **Source:** These values are typically provided as standard HTTP headers in the incoming request.
