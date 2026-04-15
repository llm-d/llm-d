# Predicted Latency-Based Scheduling

Route each inference request to the model server predicted to serve it fastest — and, optionally, only to a server predicted to meet its TTFT/TPOT SLO.

This path is for operators who want to **adopt** predicted latency-based scheduling in an existing llm-d deployment. For what the component is and how it works internally — the plugin pipeline, the ML model, scaling characteristics, the full metric list — see [architecture/advanced/latency-predictor.md](../../architecture/advanced/latency-predictor.md).

## When to Pick This Path

Pick it when:

- Your workload has **high variance in prompt and completion length**, and queue depth alone is a poor proxy for true load.
- Your clients can express **per-request latency SLOs** (interactive vs. batch) and you want the gateway to enforce them.
- Static weight tuning between cache affinity and load has become **fragile** as traffic shifts.

Skip it when your pool is **heterogeneous** — mixed GPU types, model variants, or serving configurations in the same pool will produce inaccurate predictions, because the predictor assumes a single pod shape.

## Prerequisites

- A working Inference Gateway, `InferencePool`, and at least one model server. If you don't have this, start with [getting-started/quickstart.md](../../getting-started/quickstart.md).
- Helm access to the chart used to deploy the EPP and inference pool.
- A **homogeneous** pool — same GPU type, same model weights, same serving config across every pod.
- If you plan to use SLO headers: a **fully streaming** workload (`"stream": true` on every request).

## Deploy

Enable the predictor by setting `inferenceExtension.latencyPredictor.enabled=true` when installing or upgrading the chart. This wires up the full plugin pipeline automatically — prediction, cache affinity filtering, SLO tier gating, scoring, admission control, and weighted endpoint selection. The SLO plugins stay idle until a request actually carries SLO headers, so it's safe to enable even for traffic that doesn't use SLOs yet.

```bash
helm install <release> . \
  --set inferencePool.modelServers.matchLabels.app=<your-model-label> \
  --set inferenceExtension.latencyPredictor.enabled=true \
  --set inferenceExtension.monitoring.prometheus.enabled=true \
  --set provider.name=gke \
  -f values.yaml
```

### If You Want SLO Enforcement: Turn On Streaming Mode

The `predicted-latency-producer` plugin has a `streamingMode` parameter. Its default (`false`) trains on end-to-end latency and does not train TPOT — fine for routing-only use, but incompatible with `x-slo-ttft-ms` / `x-slo-tpot-ms`. **Set `streamingMode: true` whenever you use SLO headers**, and make sure every request is actually streamed.

Override it via `inferenceExtension.pluginsCustomConfig` in `values.yaml`:

```yaml
inferenceExtension:
  latencyPredictor:
    enabled: true
  pluginsCustomConfig: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
      - type: predicted-latency-producer
        parameters:
          streamingMode: true
      # ...remaining plugins from the default pipeline...
```

### Scoring Strategy

`latency-scorer` exposes `headroomSelectionStrategy` for SLO-annotated requests. Start with the default (`least`, bin-pack toward the SLO boundary). Switch to `most` (spread toward the endpoint with the most slack) only if you observe SLO violations clustering on the pods closest to the boundary during spikes. See the [architecture doc](../../architecture/advanced/latency-predictor.md#scoring-strategy) for the full mechanics.

## Send Requests

Once enabled, latency-based scheduling works on every request — no header changes needed. The gateway picks the endpoint with the lowest predicted latency.

To opt an individual request into SLO-aware routing, add one or both headers:

- `x-slo-ttft-ms` — Time-to-first-token SLO in milliseconds.
- `x-slo-tpot-ms` — Time-per-output-token SLO in milliseconds.

Example:

```bash
export GW_IP=$(kubectl get gateway/inference-gateway -o jsonpath='{.status.addresses[0].value}'):80

curl -v $GW_IP/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'x-slo-ttft-ms: 200' \
  -H 'x-slo-tpot-ms: 50' \
  -d '{
    "model": "Qwen/Qwen3-32B",
    "prompt": "Explain the difference between prefill and decode.",
    "max_tokens": 200,
    "temperature": 0,
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

Sheddable requests (priority < 0) are rejected at admission when no endpoint can meet the SLO, rather than routed to a guaranteed miss.

## Verify

Once traffic is flowing, confirm three things in Prometheus (see the [architecture doc](../../architecture/advanced/latency-predictor.md#observability) for the metric reference):

1. **Predictions are being produced.** `inference_objective_request_ttft_prediction_duration_seconds` has non-zero samples. If it stays empty, the predictor sidecar is not being called — tail the EPP logs for `predicted-latency-producer` errors.
2. **Predictions track reality.** Compare `inference_objective_request_predicted_ttft_seconds` against `inference_objective_request_ttft_seconds` over a rolling window. A healthy deployment converges to within a few percent after warmup.
3. **SLOs are being honored.** If you're sending SLO-annotated traffic, `inference_objective_request_ttft_slo_violation_total` and `..._tpot_slo_violation_total` should increment only under genuine saturation.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Prediction duration metrics empty | Predictor sidecar unreachable — EPP falls back to composite heuristic scoring. Check sidecar readiness and `PREDICTION_SERVER_URL`. |
| Large, persistent drift between predicted and actual TTFT | `streamingMode` mismatch (set to `false` on a streaming workload, or vice versa), or workload drifted outside the training window. |
| High TPOT SLO violation rate at low QPS | `streamingMode: false` — TPOT is not being trained. Flip it to `true` and restart. |
| SLO violations cluster on a few pods during spikes | Scoring strategy is `least`; try `most` for more headroom at the cost of utilization. |
| Prediction-based routing degrades to baseline | Predictor error or sidecar restart — expected fallback, not a failure. Investigate sidecar logs. |

## Related

- [Latency Predictor Architecture](../../architecture/advanced/latency-predictor.md) — plugin pipeline, ML model, scaling characteristics, metric reference.
- [llm-d/llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) — source for the EPP plugins and per-plugin configuration references.
- [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor) — source for the training and prediction server Python code.
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms) — design rationale and benchmark results.
