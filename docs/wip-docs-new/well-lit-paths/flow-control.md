# Flow Control

Model service operators often consolidate multiple workloads onto the same set of resources leveraging a **multi-tenant** deployment pattern.

Additionally, server latency curves are non-linear with intense **saturation dynamics** - once a server crosses a utilization threshold, latency spikes sharply and quality of service collapses for all requests.

**Flow Control** considers these dynamics.

### Multi-Tenant Prioritization

Multi-tenant deployments have additional considerations beyond single workload:
* Certain tenants are **higher-priority** than others (e.g. paid vs unpaid)
* Certain tenants have **different-SLOs** than others (e.g. batch vs online)
* Certain tenants are more active than others - we want **fairness** between them

Flow control enables us to consider these dynamics in scheduling requests. This enables platform teams to consolidate multiple workloads onto the same resources:

```
SINGLE TENANT                    MULTI-TENANT
  ─────────────                    ────────────

  [A] ──▶ [ Model ]              [A] ╲
                                 [B] ──▶ [ Model ]
  [B] ──▶ [ Model ]              [C] ╱

  [C] ──▶ [ Model ]

  One deployment per customer    One deployment, many customers
```
### Single Workload "No-Regret" Scheduling

Flow control enables "no-regret" scheduling, holding back requests in the saturation regime until load has reduces on at least one of the model servers.

By delaying the scheduling decision until the actual load subsides, the EPP can make a better decision about where to land the request.

```
   ┌───┐  req  ┌──────────────────────────┐         ┌─────────────┐
   │ A │──────▶│   ┌──────────────────┐   │--------▶│ Server 1    │
   └───┘       │   │  Request Queue   │   │         │ [█████] FULL│
               │   │ ░░░░░░░░░░░░░░░  │   │         └─────────────┘
   ┌───┐       │   │  [R][R][R][R][R] │   │         ┌─────────────┐
   │ B │──────▶│   └──────────────────┘   │--------▶│ Server 2    │
   └───┘       │   ─ checks load          │         │ [█████] FULL│
               │   ─ queues reqs if       │         └─────────────┘
   ┌───┐       │     detects saturation   │         ┌─────────────┐
   │ C │──────▶│   ─ releases reqs when   │────────▶│ Server 3    │
   └───┘       │     capacity opens       │         │ [███░░] 60% │
               └──────────────────────────┘         └─────────────┘                         
```               

## Deploy

See the [Flow Control guide](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling) for configuration within the Intelligent Inference Scheduling deployment.

## Architecture

WIP!!

![Flow Control](./images/flow-control.svg)

Flow control is configured in the EPP's `EndpointPickerConfig` -- no separate deployment required. The EPP evaluates each incoming request's priority band and applies admission decisions based on real-time saturation detection. The `saturationDetector` monitors KV-cache utilization or request concurrency against a configurable threshold (default: 0.85). Priority bands are configured with `maxQueueSize` limits, and a `flowIdentifier` header (e.g., `x-client-id`) enables per-tenant fairness enforcement.

Sheddable requests flow through a **queue** that retries as capacity becomes available. When saturation clears, queued requests are dispatched in priority order. This also enables **scale-to-zero**: when no pods are running and a request arrives, the queue holds it while the autoscaler provisions new pods (2-7 minutes for model loading) rather than returning a 5xx error.

