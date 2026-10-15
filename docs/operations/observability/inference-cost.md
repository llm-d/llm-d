# Track Inference Cost

Install [inference cost tracking](../../../guides/recipes/observability/inferencecost/README.md) to generate OpenCost metrics and three special Prometheus gauges under the `llm_` prefix. These metrics join Kubernetes allocation costs with vLLM token throughput to produce per-model cost attribution that neither system can produce alone.

| Metric | Labels | What it measures | Why it matters |
|--------|--------|-----------------|----------------|
| `llm_total_hourly_cost` | `model_name`, `model_version`, `namespace`, `cost_basis`, `workload_type` | Instantaneous hourly infrastructure cost rate ($/hour) attributed to the model — not a cumulative counter | Tracks real-time GPU+CPU+RAM spend per model. Use `cost_basis="allocation"` for chargeback (max(request,usage) × price + idle/shared share) or `cost_basis="usage"` for efficiency analysis (actual consumption only) |
| `llm_cost_per_million_tokens` | `model_name`, `model_version`, `namespace`, `cost_basis`, `phase`, `allocation_method`, `workload_type` | Infrastructure cost per 1 M tokens | Primary unit-economics metric. `phase` is empty for blended (prompt+generation combined), `prompt` for input-only, or `generation` for output-only. `allocation_method` reflects how costs were split across prefill and decode phases (see table below) |
| `llm_cache_savings_fraction` | `model_name`, `model_version`, `namespace`, `workload_type` | Fraction of prompt tokens served from the KV cache (0–1) | Quantifies the cost reduction from prefix caching. Zero when caching is disabled, no cache hits occurred, or `vllm:prefix_cache_hits_total` is missing |

**`allocation_method` values for `llm_cost_per_million_tokens`:**

| Value | When it applies |
|-------|----------------|
| `compute_time` | Costs split by vLLM prefill/decode time — most accurate |
| `prefix_caching_off` | Time-based split, but prefix caching is disabled |
| `multiplier` | Fixed 2.5× ratio used when timing metrics are unavailable |
| *(empty)* | No tokens processed in the window, or the model-name join failed |

> [!NOTE]
> The `workload_type` label is currently always `inference`. Future values may include `training`, `fine-tuning`, etc.

All three metrics are scraped from OpenCost's `/metrics` endpoint (port 9003) and flow into Prometheus via a ServiceMonitor. Quick check:

```bash
kubectl port-forward -n llm-d-monitoring svc/opencost 9003:9003
curl -s http://localhost:9003/metrics | grep llm_
```

For setup instructions, see [Inference Cost Tracking](../../../guides/recipes/observability/inferencecost/README.md).
