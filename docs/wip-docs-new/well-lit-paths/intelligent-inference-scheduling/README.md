# Intelligent Inference Scheduling

llm-d's **Endpoint Picker (EPP)** replaces naive load balancing with LLM-aware scheduling, routing each request to the best model server based on real-time signals -- prefix cache locality, KV-cache utilization, queue depth, and optionally predicted latency.

## When To Use

This path is always active -- the question is which scheduling mode to enable:

| Mode | What It Adds | When To Use |
|---|---|---|
| [**Default**](./default.md) | Prefix-cache and load-aware routing | Any workload, start here |
| [**Predicted Latency**](./predicted-latency.md) | ML-predicted TTFT/TPOT for SLO-aware routing | SLO-critical workloads with heterogeneous request costs |
| [**Flow control**](./flow-control.md) | (priority queuing, fairness, admission control) is a configuration option within the EPP that can be enabled alongside any scheduling mode. It is not a separate mode -- it adds admission control and tenant isolation to whatever scoring profile is active.
| [**Precise Prefix Cache**](./precise-prefix-cache-aware-routing.md) | Exact block-level cache tracking via KV-Events | When approximate routing is not enough (e.g., multi-modal, hybrid-model, near saturation regime) |

> [!NOTE]
> llm-d team recommends starting with the default configuration.

## Key Decisions

Start with [load-aware and approximate prefix cache scheduling](./default.md) (2:2:1 weights for queue depth, KV-cache utilization, and prefix cache affinity). Upgrade to precise prefix cache or predicted latency based on what you observe -- each sub-page describes when to make that transition.

## Observability

| Metric | What It Tells You |
|---|---|
| `inference_objective_request_total` | Request rate -- verify traffic is flowing |
| `inference_objective_request_error_total` | Error rate -- should be near zero |
| `inference_extension_scheduler_e2e_duration_seconds` (P99) | Scheduling latency -- spikes indicate EPP overload |
| `inference_extension_prefix_indexer_hit_ratio` (P90) | Cache effectiveness -- low values suggest upgrade to precise mode |
| `vllm:num_requests_waiting` | Queue depth per pod -- growing unbounded means saturation |
| `vllm:kv_cache_usage_perc` | KV-cache pressure -- >90% means cache evictions are likely |

### Grafana Dashboards

llm-d ships four Grafana dashboards in `docs/monitoring/grafana/dashboards/`:

- **llm-d vLLM Overview** -- request rate, latency, throughput
- **llm-d Failure & Saturation Indicators** -- error rate, queue depth alerts
- **llm-d Diagnostic Drill-Down** -- per-pod latency breakdown, cache analysis
- **inference-gateway v1.0.1** -- EPP-specific plugin latencies

### Verify It's Working

```bash
# Health check
./guides/operations/healthcheck/healthcheck.sh -e http://${GATEWAY_IP}:8080 -m Qwen/Qwen3-32B

# Check EPP is routing (not just passing through)
kubectl logs -n ${NAMESPACE} -l app=epp | grep "selected endpoint"
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| High error rate | Model OOM, GPU errors | Check vLLM pod logs for CUDA/OOM errors; reduce `gpu-memory-utilization` |
| Scheduling latency spikes | EPP overloaded | Check EPP pod CPU/memory; review scorer plugin count |
| Queue depth growing unbounded | Insufficient replicas | Enable [autoscaling](../workload-autoscaling.md) or add replicas |
| Low cache hit ratio | Workload has low prefix sharing | Expected behavior -- reduce `prefix-cache-scorer` weight |
| EPP pod crash-looping | Invalid EndpointPickerConfig | Check ConfigMap YAML syntax |

## Deployment

See the [Intelligent Inference Scheduling guide](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling) for step-by-step deployment. Works with vLLM on NVIDIA, AMD, Intel XPU, Intel Gaudi, Google TPU, and CPU. SGLang is supported on GPU hardware.

For architecture details, see [EPP Scheduling](/docs/architecture/core/epp/scheduling).
