# Workload Autoscaling

GPU utilization is a meaningless signal for LLM scaling decisions -- a server processing 5 requests shows similar GPU utilization to one processing 50. These metrics reflect saturation *after* it has already occurred, not predictive signals that enable proactive scaling.

llm-d provides two autoscaling paths that use inference-specific signals: queue depth, KV-cache utilization, and running request counts.

## When To Use

| Scenario | Recommendation |
|---|---|
| Single model, single GPU type | HPA + IGW Metrics -- simplest setup |
| Same model on mixed hardware (A100 + L4) | WVA -- cost-optimized scaling across variants |
| Multi-model serving | WVA per model -- each model gets independent scaling |
| Scale-to-zero required | Either -- both support it, but KEDA is more mature for production |

## Key Decisions

### Choosing an Autoscaling Path

| | HPA + IGW Metrics | Workload Variant Autoscaler (WVA) |
|---|---|---|
| **Best for** | Single model, homogeneous hardware | Multiple models or hardware types |
| **Signals** | Queue depth, running requests | KV-cache utilization, queue depth, configurable energy and performance budgets |
| **Cost optimization** | No | Yes -- scales cheapest variant first |
| **Scale-to-zero** | Yes | Yes |
| **Complexity** | Low -- standard HPA | Medium -- WVA controller + HPA |

**HPA + IGW Metrics** is the simpler path. The EPP's [flow control](./intelligent-inference-scheduling/flow-control.md) layer exposes Prometheus metrics. A Prometheus Adapter or KEDA bridges these to the Kubernetes External Metrics API, and standard HPA scales model server deployments.

**Workload Variant Autoscaler (WVA)** is for operators running the same model across different hardware configurations. A **variant** is a scale target with a specific cost profile -- e.g., Llama-3.1-8B on A100 (`variantCost: 100`) vs L4 (`variantCost: 30`) vs CPU (`variantCost: 5`). The WVA monitors saturation, scales up on the cheapest available variant, and scales down the most expensive variant only after simulating that remaining replicas can absorb the load. It emits `wva_desired_replicas` as a Prometheus metric that standard HPA/KEDA acts on -- it never scales deployments directly. See the [WVA paper on arXiv](https://arxiv.org/abs/2603.09730) for the algorithm description.

> [!NOTE]
> On clusters with KEDA installed (common on OpenShift), KEDA overwrites Prometheus Adapter's APIService registration. Use KEDA's `prometheus` trigger type instead.

### Scale-to-Zero

Both paths support scaling model server pods to zero when idle. When a new request arrives, the EPP's [flow control](./intelligent-inference-scheduling/flow-control.md) layer queues it while the autoscaler provisions new pods. Requests wait (2-7 minutes for model loading) rather than fail with 5xx errors.

Particularly valuable for:
- Development clusters with intermittent usage
- Internal applications that don't need 24/7 GPU allocation
- Multi-model deployments where some models are rarely queried

**Implementation options:**
- **KEDA** with Prometheus trigger (recommended for production)
- **Native HPA** with `HPAScaleToZero` alpha feature gate (Kubernetes 1.29+)

### Progressive Tuning

| Parameter | Default | Start Here | Adjust When |
|---|---|---|---|
| HPA `targetValue` (queue depth) | -- | 5 per replica | Increase if scaling is too aggressive; decrease if requests queue too long |
| HPA `stabilizationWindowSeconds` | 300 | 300 (scale-down) | Reduce for faster scale-down in dev; increase for production stability |
| WVA `variantCost` | -- | Relative to GPU cost | Set proportional to actual GPU-hour cost (e.g., A100=100, L4=30, CPU=5) |
| Saturation threshold | 0.85 | 0.85 | Lower to 0.70 for latency-sensitive workloads; raise to 0.90 if capacity is underutilized |
| HPA `minReplicas` | 1 | 1 | Set to 0 for scale-to-zero (requires KEDA or `HPAScaleToZero` feature gate) |

## Observability

| Metric | What It Tells You |
|---|---|
| `inference_extension_flow_control_queue_size` | Requests waiting for capacity -- sustained >0 means autoscaling should be active |
| `inference_objective_running_requests` | Active requests per model -- compare against HPA target to verify scaling decisions |
| `wva_desired_replicas` | WVA's recommended replica count -- compare against actual replicas to verify HPA is following |
| `inference_pool_ready_pods` | Actual ready pods -- should track `wva_desired_replicas` within HPA stabilization window |
| `inference_pool_average_kv_cache_utilization` | Pool-wide cache pressure -- WVA uses this as a primary scaling signal |

### Verify Autoscaling Is Working

```bash
# Check HPA status and current metrics
kubectl get hpa -n ${NAMESPACE}

# Verify Prometheus Adapter is serving EPP metrics
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1" | jq '.resources[].name'

# For WVA, check controller logs
kubectl logs -n ${NAMESPACE} -l app=workload-variant-autoscaler
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| HPA shows `<unknown>` for metric value | Prometheus Adapter not configured or metric name mismatch | Verify adapter rules match EPP metric names; check `kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1"` |
| Scaling too slow -- requests queue for minutes | HPA stabilization window too long | Reduce `stabilizationWindowSeconds` for scale-up (default 0) and scale-down (default 300) |
| Scaling too aggressive -- constant up/down | HPA target too sensitive to short bursts | Increase `stabilizationWindowSeconds`; raise HPA `targetValue` |
| WVA not scaling cheapest variant first | `variantCost` values not set correctly | Verify cost values are proportional to actual GPU costs |
| Scale-to-zero not triggering | `minReplicas` > 0 or KEDA not installed | Set `minReplicas: 0` in HPA; verify KEDA is installed and ScaledObject is configured |
| Pods scaled up but requests still queuing | Model loading time (2-7 min) | Expected -- flow control queues requests during cold start. Consider keeping `minReplicas: 1` for latency-sensitive models |

## Current Limitations

- WVA does not yet support [Wide Expert Parallelism](./wide-expert-parallelism.md) LWS deployments
- Independent scaling of prefill and decode pools in [P/D disaggregation](./prefill-decode-disaggregation.md) is planned
- Token-based scaling (replacing percentage-based) is on the roadmap

## Deployment

See the [Workload Autoscaling guide](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling) for step-by-step deployment covering both the HPA and WVA paths.

For architecture details on the saturation analyzer, variant optimization, and configuration reference, see [Autoscaling architecture](/docs/architecture/advanced/autoscaling).
