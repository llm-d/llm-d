# Autoscaling

With autoscaling, model servers are added or removed automatically to keep
serving capacity aligned with inference demand. llm-d scales on signals emitted
by the Endpoint Picker (EPP) — queued requests, active concurrency, token
backlog, or estimated latency against an SLO — because accelerator utilization
is an unreliable proxy for LLM load: GPU utilization can sit near 100% during
active batching whether the server is lightly loaded or saturated.

## KEDA + EPP Metrics (Recommended)

KEDA's Prometheus scaler reads EPP metrics, evaluates them into a scaler value,
and creates and owns the HPA that scales model server replicas. There is no
custom controller and no Prometheus Adapter in the loop.

See [KEDA with EPP Metrics](./keda-epp.md) for the pipeline, the metric
isolation rules, ownership semantics, scale-to-zero, and limitations.

Four signals share that pipeline, each with its own deployable guide:

| Signal | Scales on | Guide |
|---|---|---|
| **Queue depth** | Absolute per-replica queued-request and running-request targets | [keda-epp-queue](../../../../guides/workload-autoscaling/keda-epp-queue/README.md) |
| **Pool saturation** | A normalized saturation ratio (0.0–1.0+), more portable across hardware | [keda-epp-saturation](../../../../guides/workload-autoscaling/keda-epp-saturation/README.md) |
| **Token backlog** | Seconds of prefill queue wait plus KV cache occupancy — for widely varying prompt sizes | [keda-epp-token-aware](../../../../guides/workload-autoscaling/keda-epp-token-aware/README.md) |
| **Estimated latency** | Predicted (or measured) TTFT/TPOT against latency SLOs | [slo-aware](../../../../guides/workload-autoscaling/slo-aware/README.md) |

The latency-driven variant is the one design worth reading separately: it
collapses its triggers into a single formula whose output is the desired replica
count. See
[SLO-Aware Autoscaling with KEDA — the control law](./slo-aware-keda.md) for the
derivation, and the
[SLO-aware autoscaling guide](../../../../guides/workload-autoscaling/slo-aware/README.md)
for the deployable setup.

## Workload Variant Autoscaler (Deprecated)

> [!WARNING]
> WVA is deprecated. Use KEDA + EPP for new deployments.

WVA was a global optimizer and controller that, given an inventory of available
accelerators, decided how to place model servers — potentially serving different
base models, on heterogeneous hardware, in disaggregated prefill/decode roles —
onto those accelerators, publishing a `wva_desired_replicas` metric for an HPA
to consume. It accounted for cost across variants, fair-shared scarce
accelerators, and incorporated pending pods into its decisions.

See [Workload Variant Autoscaler (WVA)](./wva.md) for the retained design
reference and [Migrating off WVA](./wva.md#migrating-off-wva) for the move to
KEDA + EPP.

## What Changed with the Deprecation

KEDA + EPP does not replace WVA feature for feature. Cost-aware placement across
variants has no current equivalent; the rest is covered, though in some cases by
a different layer than the autoscaler:

| Capability | WVA (deprecated) | KEDA + EPP |
|---|---|---|
| **Multiple variants of one model** | Optimally placed across models and topologies to minimize cost | Each Deployment scales independently; no cost-aware preference between variants |
| **Limited accelerators** | Fair-share allocation across pools | Handled below the autoscaler by [Kueue](../../../../guides/workload-autoscaling/kueue-rebalancing/README.md) — per-model quota floors in a shared cohort, with borrowing and preemption |
| **Pending pods awareness** | Incorporated pending (unscheduled) pods into decisions | Available — a trigger can read replica counts from kube-state-metrics; the [slo-aware](../../../../guides/workload-autoscaling/slo-aware/README.md) path discounts its ask by `readyReplicas/replicas` |
| **Strong latency SLOs** | Learned supply/demand dynamics (experimental) | Scales directly on estimated latency vs. SLO ([slo-aware](../../../../guides/workload-autoscaling/slo-aware/README.md)) |
| **Scale to zero** | Supported | Supported (KEDA, without the `HPAScaleToZero` feature gate) |
| **Operational complexity** | Medium — requires the WVA controller (plus a metrics adapter) | Low — KEDA and Prometheus |

If a deployment depends on cost-aware placement across variants or on fair
sharing of a scarce accelerator pool, stay on WVA for now and track the
[workload autoscaling guides](../../../../guides/workload-autoscaling/README.md)
for a replacement.
