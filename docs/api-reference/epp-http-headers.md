# EPP HTTP Headers Reference

This document describes the HTTP headers that the [Endpoint Picker (EPP)](../architecture/router/epp.md) inspects to manage and control inference requests, specifically for flow control, performance management, and request classification.

## Request Classification and Flow Control

These headers allow the EPP to identify the request's goals, group them for fair resource allocation, and handle model-specific targeting.

| Header | Description |
| --- | --- |
| `x-llm-d-inference-objective` | Specifies the name of the `InferenceObjective` resource associated with the request. The EPP uses this to look up the corresponding objective resource in the **same namespace as the InferencePool** to apply the defined priority and performance goals. |
| `x-llm-d-inference-fairness-id` | Provides a tenant or flow identifier (`FairnessID`) for grouping requests in fairness-based flow control. Requests with the same ID share a flow queue within a priority band, or form the `<tenant>` prefix when `requestHandler.composeFairnessIDs: true` is configured. If omitted and no session or lineage attributes resolve, the EPP defaults to `default-flow`. |
| `x-llm-d-inference-ttl` | Specifies a per-request Flow Control queue-wait timeout as a Go duration string (for example, `15s` or `500ms`). Tightens the priority band's configured queue-wait bound (or sets one when the band has no timeout); values exceeding the band bound are clamped to the band bound, and invalid or non-positive values are ignored. |
| `x-llm-d-model-name-rewrite` | Specifies the target model name to be used for the request. This is an alternative approach to model name rewriting; while the `InferenceModelRewrite` API provides rule-based rewriting on the server side, this header allows for an explicit, per-request override. When present, the EPP uses this value to override the model name in the request body and for recording model-specific metrics. |

## Session and Agent Lineage

These headers correlate multi-turn conversations and multi-agent execution trees within a tenant bucket. Cross-tenant gateways must strip `x-llm-d-inference-fairness-id` and `x-llm-d-inference-objective` from untrusted clients and stamp them from authenticated identity. Lineage headers instead subdivide and correlate work within the authenticated tenant scope. See [Workload and State Model](../architecture/router/workload-model.md) for the gateway trust boundary and three replica-local state domains.

### Ingress Gateway Policy

Multi-tenant deployments fronting untrusted clients enforce three gateway rules upstream of Envoy `ext_proc`:

1. **Strip Untrusted Entitlements**: Strip external values of `x-llm-d-inference-fairness-id`, `x-llm-d-inference-objective`, deprecated `x-gateway-*` aliases, `x-llm-d-model-name-rewrite`, `x-llm-d-slo-*` / `x-slo-*` (unless client deadline tightening is explicitly permitted), and external `x-session-token*` headers (unless stateless `encoded_endpoint_header` affinity is intentionally exposed to clients; prefer `strategy: session_id` for untrusted clients). Per-request queue-wait tightening (`x-llm-d-inference-ttl`) only shortens queue-wait bounds and is safe to pass through from untrusted clients.
2. **Stamp Non-Empty Tenant**: Stamp `x-llm-d-inference-fairness-id` with the authenticated tenant identity (or an unauthenticated fallback such as `anonymous`) on every request. If a gateway strips the fairness header without stamping a fallback value, the tenant remains empty and the EPP Director falls back to client-supplied session attributes even when `composeFairnessIDs: false`, allowing unauthenticated session IDs to mint separate flow queues or collide with stamped tenant IDs.
   - **Fallback matrix when `x-llm-d-inference-fairness-id` is missing**:
     - `default-tenant/<derived-root>` when `composeFairnessIDs: true` and `agent-lineage` resolves `derived-root`.
     - `<derived-root>` when `composeFairnessIDs: false` and `agent-lineage` resolves `derived-root`.
     - `<agent-identity>` when `derived-root` is unset and `agent-identity` resolves a session header (whether `composeFairnessIDs` is `true` or `false`).
     - `default-flow` when neither fairness nor session/lineage attributes resolve.
3. **Clamp Priority**: Clamp `x-llm-d-inference-objective` to the caller's authorized tier ceiling, allowing intra-program sub-agents to self-downgrade priority without escalating it.

| Header | Description |
| --- | --- |
| `x-session-id` | Identifies a stateful conversation thread for sticky session routing (`session-affinity-scorer` and `session-affinity-filter` with `strategy: session_id`) and session attribute extraction (`session-id-producer` configured with `headerName: x-session-id`). Not included in the default header list of `agent-identity` or `agent-lineage` unless specified via `additionalSessionHeaders`. |
| `x-session-token`, `x-session-token-prefill` | Carries base64-encoded `<namespace>/<name>` endpoint tokens for stateless sticky routing (`session-affinity-scorer` and `session-affinity-filter` with default `encoded_endpoint_header` strategy; `x-session-token-prefill` is the conventional `encodedEndpointHeaderConfig.header` for a `profileName: prefill` instance). Echoed back by clients on subsequent turns. |
| `x-claude-code-session-id` | Claude Code session identifier shared across an agent execution tree, inspected by `agent-identity` and `agent-lineage`. |
| `x-opencode-session-id`, `x-opencode-parent-session-id`, `x-parent-session-id`, `x-session-affinity` | OpenCode and generic sub-agent lineage headers. `x-opencode-session-id` identifies the current branch; `x-opencode-parent-session-id` and `x-parent-session-id` identify the immediate parent branch so `agent-lineage` resolves `derived-root`; `x-session-affinity` provides parent and fork affinity fallback (also inspected by `agent-identity`). |
| `x-codex-turn-metadata`, `session-id`, `session_id` | Codex CLI session identifiers. The `agent-lineage` plugin parses the root `session_id` field from JSON `x-codex-turn-metadata` (up to 8 KiB), falling back to `session-id` and `session_id` (also inspected by `agent-identity`). |

## Service Level Objectives (SLOs)

These headers are used by admission control and load balancing plugins to make decisions based on latency targets.

| Header | Description |
| --- | --- |
| `x-llm-d-slo-ttft-ms` | Specifies the target Time To First Token (TTFT) in milliseconds. Used by plugins to determine if a request can be admitted while meeting the latency goal. |
| `x-llm-d-slo-tpot-ms` | Specifies the target Time Per Output Token (TPOT) in milliseconds. Used for admission control based on predicted or observed token generation latency. |

## Deprecated Aliases

llm-d Router v0.9.0 and later accept the previous EPP-managed header names as deprecated read aliases. New integrations and examples should use the canonical `x-llm-d-*` names. Earlier releases expect the previous names, so mixed-version fleets should coordinate the header migration during rolling upgrades. See the [llm-d Router releases](https://github.com/llm-d/llm-d-router/releases) page for available versions.

If a request sends both a canonical header and its deprecated alias, a non-empty canonical `x-llm-d-*` value wins deterministically (an empty canonical header falls back to the deprecated alias). No alias removal release or date has been announced; treat aliases as temporary compatibility support and prefer the canonical names for all new clients.

| Deprecated alias | Canonical header |
| --- | --- |
| `x-gateway-inference-fairness-id` | `x-llm-d-inference-fairness-id` |
| `x-gateway-inference-objective` | `x-llm-d-inference-objective` |
| `x-gateway-model-name-rewrite` | `x-llm-d-model-name-rewrite` |
| `x-slo-ttft-ms` | `x-llm-d-slo-ttft-ms` |
| `x-slo-tpot-ms` | `x-llm-d-slo-tpot-ms` |

This rename applies only to llm-d/EPP-managed user and control headers. [Gateway API Inference Extension (GAIE) Endpoint Picker Protocol](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/docs/proposals/004-endpoint-picker-protocol) headers, such as `x-gateway-destination-endpoint*`, are unchanged.

## Response Headers

These headers are set by the EPP on responses sent back to the client.

| Header | Description |
| --- | --- |
| `x-llm-d-flow-queue-duration-ms` | Emitted on served responses admitted through Flow Control, reporting integer milliseconds spent waiting in Flow Control admission (`0` indicates dispatch without measurable queueing). Absent when Flow Control is disabled or when a request is rejected before dispatch. |
| `x-llm-d-request-dropped-reason` | Indicates why a request was dropped by flow control or admission. Present on `429`, `503`, and `500` responses generated by the EPP (not forwarded from a model server). The value uses a two-prefix scheme: `rejected-*` means the request never reached an inference server, `evicted` means it was dispatched and then terminated. See the table below for possible values. |
| `x-session-token`, `x-session-token-prefill` | Injected by `session-affinity-scorer` or `session-affinity-filter` when running with the default `encoded_endpoint_header` strategy. Contains the base64-encoded `<namespace>/<name>` of the selected decode (or prefill) endpoint so clients can echo it on subsequent turns to maintain endpoint affinity. |

### Dropped Reason Values

| Value | HTTP Status | Meaning |
| --- | --- | --- |
| `rejected-saturated` | `429 Too Many Requests` | Pool or priority band is at capacity and cannot accept the request. |
| `rejected-flow-limit` | `429 Too Many Requests` | Per-flow queue capacity limit was reached (`ErrFlowAtCapacity`). |
| `evicted` | `429 Too Many Requests` | Request was terminated after dispatch to reclaim capacity for a higher-priority request. |
| `rejected-ttl-expired` | `429 Too Many Requests` | Request entered the queue but its TTL expired before dispatch while the pool still had endpoints. |
| `rejected-no-endpoints` | `503 Service Unavailable` | Pool has no serving endpoints, or a queued request's TTL expired while the pool was empty. |
| `rejected-context-cancelled` | `503 Service Unavailable` | Client disconnected while the request was queued. |
| `rejected-shutting-down` | `503 Service Unavailable` | The EPP flow controller is draining queued requests during a graceful shutdown. |
| `rejected-internal` | `500 Internal Server Error` | An internal error occurred in the flow controller or scheduler. |

The prefix convention helps consumers decide retry strategy: `rejected-*` means no inference work was done (cheap to retry), while `evicted` means GPU cycles were consumed (factor into backoff).

---

## Implementation Notes

- **Case Sensitivity:** All header lookups are case-insensitive.
- **Source:** These values are typically provided as standard HTTP headers in the incoming request.
