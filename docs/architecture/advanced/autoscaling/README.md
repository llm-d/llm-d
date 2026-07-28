# Autoscaling

With autoscaling, model servers are added or removed automatically to keep serving capacity aligned with inference demand. llm-d autoscalers consume three categories of scaling signals — supply-side, demand-side, and SLO-driven — surfaced through two complementary systems:

- **KEDA + EPP Metrics** - Uses KEDA's Prometheus scaler with demand-side
  signals (EPP queue depth and active request counts). KEDA creates and manages
  the HPA that scales model server replicas. This path is well-suited for homogeneous deployments where each target model-server pool can be isolated by metrics and scaled independently.

  See [KEDA + EPP Metrics](./hpa-epp.md) for complete design details.

- **HPA + WVA Metrics** - A global optimizer that, given an inventory of available accelerators, determines how to optimally place model servers — potentially serving different base models — onto those accelerators. WVA consumes supply-side signals (KV cache utilization, model server queue depth) or SLO-driven signals to proactively meet latency targets specified in its configuration. It accounts for heterogeneous hardware, disaggregated serving roles (prefill, decode, or both), and changing traffic patterns. When the accelerator inventory is insufficient to meet all targets, WVA degrades gracefully by prioritizing placement decisions that maximize overall SLO attainment.

  See [HPA + WVA Metrics](./hpa-wva.md) for complete details on the WVA design.

## Features Matrix

| | [KEDA + EPP Metrics](./hpa-epp.md) | [HPA + WVA Metrics](./hpa-wva.md) |
|---|---|---|
| **Scaling Signals** | EPP Flow Control queue size and running request count | KV cache utilization, model server queue depth, SLO targets (**Experimental**), IGW queue size (**Experimental**) |
| **Multiple-Variants** | Unsupported | Supported — optimally places across models and topologies to minimize cost |
| **Limited Accelerators** | First come, first served | Fair share allocation |
| **Scale to zero** | Supported | Supported |
| **Strong Latency SLOs** | Not guaranteed | Supported by learning supply/demand dynamics and scaling proactively to meet targets  (**Experimental**) |
| **Pending Pods Awareness** | Unsupported — external metrics do not account for pending (unscheduled) pods | Supported — incorporates pending pod state into scaling decisions |
| **Operational Complexity** | Low - Requires KEDA and Prometheus | Medium - Requires WVA controller |

> [!NOTE]
> KEDA supports scale-to-zero without the native Kubernetes `HPAScaleToZero`
> feature gate. For WVA-specific requirements, see the linked design
> documentation.

## Choosing an Approach

- **KEDA + EPP Metrics** - Homogeneous hardware, independently scaled model-server pools, demand-side signals only.
- **HPA + WVA Metrics** - Heterogeneous hardware, multiple serving variants, supply-constrained environments, and/or SLO-driven scaling.
