# Autoscaling

With autoscaling, model servers are added or removed automatically to keep serving capacity aligned with inference demand. llm-d autoscalers consume three categories of scaling signals — supply-side, demand-side, and SLO-driven — surfaced through two complementary systems:

- **HPA/KEDA** - Uses demand-side signals (EPP queue depth and active request counts) to scale model server replicas via Kubernetes HPA or KEDA. Well-suited for homogeneous deployments where each model scales independently.

  See [HPA/KEDA](./hpa-keda.md) for complete details on the HPA/KEDA design.

- **Workload Variant Autoscaler (WVA)** - A global optimizer that, given an inventory of available accelerators, determines how to optimally place model servers — potentially serving different base models — onto those accelerators. WVA consumes supply-side signals (KV cache utilization, model server queue depth) or SLO-driven signals to proactively meet latency targets specified in its configuration. It accounts for heterogeneous hardware, disaggregated serving roles (prefill, decode, or both), and changing traffic patterns. When the accelerator inventory is insufficient to meet all targets, WVA degrades gracefully by prioritizing placement decisions that maximize overall SLO attainment.

  See [Workload Variant Autoscaler (WVA)](./wva.md) for complete details on the WVA design.

## Features Matrix

| | [HPA/KEDA](./hpa-keda.md) | [WVA](./wva.md) |
|---|---|---|
| **Scaling Signals** | IGW queue depth and running request count | KV cache utilization, model server queue depth, SLO targets (**Experimental**), IGW queue size (**Experimental**) |
| **Multiple-Variants** | Unsupported | Supported — optimally places across models and topologies to minimize cost |
| **Limited Accelerators** | First come, first served | Fair share allocation |
| **Scale to zero** | Supported | Supported |
| **Strong Latency SLOs** | Not guaranteed | Supported by learning supply/demand dynamics and scaling proactively to meet targets  (**Experimental**) |
| **Pending Pods Awareness** | Unsupported — external metrics do not account for pending (unscheduled) pods | Supported — incorporates pending pod state into scaling decisions |
| **Operational Complexity** | Low - Standard Kubernetes HPA/KEDA only | Medium - Requires WVA controller and `VariantAutoscaling` CRD |

> [!NOTE]
> Native Kubernetes HPA scale-to-zero requires cluster support for the `HPAScaleToZero` feature. KEDA-based scale-to-zero is an alternative when that HPA feature is not enabled. For WVA-specific requirements, see the linked design documentation.

## Choosing an Approach

- **HPA/KEDA** - Homogeneous hardware, independent per-model scaling, demand-side signals only.
- **WVA** - Heterogeneous hardware, multiple serving variants, supply-constrained environments, and/or SLO-driven scaling.
