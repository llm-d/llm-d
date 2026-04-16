# Predicted Latency Scheduling

:::caution Experimental
Predicted Latency Scheduling is experimental. It requires building the EPP from the `slo-prediction-experimental` branch. The API and behavior may change in future releases.
:::

Queue depth and KV-cache utilization are useful proxy signals, but they don't tell you how long a request will actually take. A server with 2 queued requests may be slower than one with 10, depending on the token lengths involved. Predicted latency scoring closes this gap by learning the actual relationship between server state, request characteristics, and latency from live traffic.

The EPP predicts the TTFT and TPOT each candidate endpoint would deliver for a specific request, then routes to maximize SLO headroom -- filling endpoints close to their SLO limits while keeping others free for future requests.

See [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms) for benchmarks and accuracy analysis.

## When To Use

- **SLO-critical workloads** -- per-request SLO targets via `x-slo-ttft-ms` and `x-slo-tpot-ms` headers
- **Mixed workloads** -- short and long prompts competing for the same endpoints, where queue depth alone can't distinguish cost
- **Mature deployments** -- requires sufficient traffic for the ML models to train (the system learns from live data)

> [!IMPORTANT]
> Current limitations: homogeneous pools only, streaming requests only, p90 predictions only, no P/D disaggregation support.

## Key Decisions

### Configuration

Three prediction sidecars run alongside the EPP, plus a training sidecar that continuously updates XGBoost models from completed request data. No additional infrastructure beyond the EPP pod.

Add `slo-scorer` to the existing scoring profile:

```yaml
schedulingProfiles:
  - name: default
    scorers:
      - scorerName: queue-depth-scorer
        weight: 2
      - scorerName: kv-cache-utilization-scorer
        weight: 2
      - scorerName: prefix-cache-scorer
        weight: 1
      - scorerName: slo-scorer
        weight: 3
    picker:
      pickerName: max-score-picker
```

The higher weight (3) gives predicted latency the strongest pull in routing decisions, while load and cache signals still prevent degenerate behavior.

### Progressive Tuning

| Parameter | Default | Start Here | Adjust When |
|---|---|---|---|
| `slo-scorer` weight | 3 | 3 | Reduce if prediction overhead is too high; increase if SLO misses persist despite available capacity |
| Training warmup | ~30 min | Wait 30 min | Predictions are inaccurate until the training sidecar has sufficient data |
| Headroom blend | 80% TTFT / 20% TPOT | Keep default | Shift toward TPOT for throughput-oriented workloads; keep TTFT bias for interactive workloads |
| Prediction replicas | 3 | 3 | Reduce if `inference_extension_scheduler_e2e_duration_seconds` overhead (~48ms at 10K QPS) is too high |

## Observability

| Metric | What It Tells You |
|---|---|
| `inference_extension_scheduler_e2e_duration_seconds` (P99) | Scheduling overhead -- prediction adds latency; monitor for regressions |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| SLO misses despite available capacity | Training warmup incomplete | Wait ~30 minutes for model convergence after deployment |
| Scheduling overhead too high | Too many prediction replicas or high QPS | Reduce prediction replica count; check EPP pod CPU |
| Predictions inaccurate after traffic pattern change | Model trained on old distribution | Training sidecar adapts continuously; allow ~30 min for convergence |

## Deployment

See the [Predicted Latency Scheduling guide](https://github.com/llm-d/llm-d/tree/main/guides/predicted-latency-based-scheduling) for step-by-step deployment. For architecture details on the training pipeline and scoring algorithm, see [Latency Predictor architecture](/docs/architecture/advanced/latency-predictor).
