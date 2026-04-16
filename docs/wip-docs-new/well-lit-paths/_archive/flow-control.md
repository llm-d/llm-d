# Flow Control

Flow control is a configuration option within the EPP -- not a separate scheduling mode. It adds admission control, priority queuing, and fairness enforcement to whatever scoring profile is active. It also enables [scale-to-zero](../workload-autoscaling.md#scale-to-zero) by queuing requests while pods start up.

Without flow control, burst traffic can overwhelm model servers -- causing queue buildup, noisy-neighbor effects, and cascading KV-cache evictions that degrade all subsequent requests. A single client sending a burst of 1,000 requests can starve every other tenant.

## When To Use

- **Multi-tenant deployments** -- multiple clients or teams sharing the same model pool
- **Burst traffic patterns** -- workloads with unpredictable spikes
- **Scale-to-zero** -- development clusters or low-traffic models where GPU allocation should be released when idle

**Not needed** for single-tenant deployments with steady traffic patterns.

## Key Decisions

### Configuration

```yaml
flowControl:
  priorityBands:
    - name: real-time
      priority: 100
      maxQueueSize: 1000
    - name: batch
      priority: 10
      maxQueueSize: 5000
  fairness:
    enabled: true
    flowIdentifier: "x-client-id"
  saturationDetector:
    mode: utilization      # or "concurrency"
    threshold: 0.85
```

### Progressive Tuning

| Parameter | Default | Start Here | Adjust When |
|---|---|---|---|
| `maxQueueSize` | 1000 / 5000 | Keep defaults | Increase if bursts are longer than expected; decrease if tail latency is unacceptable |
| `saturationDetector.threshold` | 0.85 | 0.85 | Lower to 0.70 if you see frequent cache evictions; raise to 0.90 if capacity is underutilized |
| `saturationDetector.mode` | `utilization` | `utilization` | Switch to `concurrency` if you have predictable per-server capacity limits |
| `flowIdentifier` | -- | Set to your tenant header | Must match the header your clients actually send (e.g., `x-client-id`, `x-tenant`) |

## Observability

Flow control exposes the recommended signals for [autoscaling](../workload-autoscaling.md):

| Metric | What To Watch For |
|---|---|
| `inference_extension_flow_control_queue_size` | Sustained values >0 mean saturation is active. Growing unbounded means capacity is insufficient. |
| `inference_objective_running_requests` | Active requests per `InferenceModel`. Compare against expected concurrency. |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Requests queued but capacity available | Saturation threshold too low | Raise `threshold` from 0.85 to 0.90 |
| One tenant getting all capacity despite fairness | `flowIdentifier` not matching actual headers | Verify clients send the expected header; check for typos |
| Batch requests never dispatched | Real-time queue always full | Increase pool capacity or raise real-time `maxQueueSize` |
| 429 errors during traffic spikes | Queue overflow -- requests being shed | Increase `maxQueueSize` or add capacity |

## Deployment

Flow control is configured in the EPP's EndpointPickerConfig -- no separate deployment required. See the EPP [configuration reference](/docs/architecture/core/epp/configuration) for the full schema.
