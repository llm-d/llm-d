# Precise Prefix Cache-Aware Routing

The default `prefix-cache-scorer` estimates which servers likely hold relevant prefix blocks. For workloads with high prefix sharing -- RAG pipelines, multi-tenant deployments with shared system prompts, document analysis -- this estimation can misroute requests, sending them to servers that don't actually hold the cached blocks.

Precise routing replaces estimation with exact knowledge. The EPP pulls up-to-date prefix cache status directly from serving instances via KV-Events, eliminating the need for additional indexing services. The block-to-pod mapping is maintained internally within the EPP.

See [KV Cache Wins You Can See](https://llm-d.ai/blog/kvcache-wins-you-can-see) for benchmarks showing the impact.

## When To Use

- **High prefix sharing** -- RAG with shared document contexts, multi-tenant with shared system prompts, long shared context windows
- **Large pools** (4+ pods) where approximate scoring diverges from reality
- **Latency-sensitive workloads** where cache misses cause unacceptable TTFT spikes

**Not needed** for workloads with low prefix sharing (unique prompts per request) or very small pools (2-3 pods).

## Key Decisions

### Configuration

Replace `prefix-cache-scorer` with `precise-prefix-cache-scorer`:

```yaml
schedulingProfiles:
  - name: default
    scorers:
      - scorerName: queue-depth-scorer
        weight: 2
      - scorerName: kv-cache-utilization-scorer
        weight: 2
      - scorerName: precise-prefix-cache-scorer
        weight: 1
    picker:
      pickerName: max-score-picker
```

Enable KV-Events on vLLM:

```yaml
env:
  - name: VLLM_KV_EVENTS_PUBLISHER_ADDR
    value: "tcp://*:5557"
  - name: VLLM_KV_EVENTS_USE_INT_BLOCK_HASHES
    value: "1"
```

### Event Delivery Modes

| Mode | When To Use |
|---|---|
| **Centralized ZMQ** (default) | Single EPP replica -- simplest setup |
| **Pod Discovery** | Multiple EPP replicas for HA -- each EPP subscribes to pods directly |

### Progressive Tuning

| Parameter | Default | Start Here | Adjust When |
|---|---|---|---|
| `precise-prefix-cache-scorer` weight | 1 | 1 | Increase to 2 if hit ratio is high but TTFT is still elevated -- cache affinity is underweighted |
| Event delivery mode | Centralized ZMQ | Centralized ZMQ | Switch to Pod Discovery when running multiple EPP replicas for HA |

## Observability

Monitor `inference_extension_prefix_indexer_hit_ratio` (P90) -- this is the primary indicator. With precise routing on a high-prefix-sharing workload, expect hit ratios above 80%. If it's below 50%, check that KV-Events are flowing:

```bash
# Verify KV-Events are being published
kubectl logs -n ${NAMESPACE} -l app=vllm | grep "KVEvent"
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Hit ratio no better than approximate mode | KV-Events not reaching EPP | Verify `VLLM_KV_EVENTS_PUBLISHER_ADDR` is set on all vLLM pods |
| Hit ratio drops after scaling | New pods not publishing events | Confirm new pods have KV-Events env vars in their deployment overlay |
| EPP latency increased | Block lookup overhead | Expected (~1ms); if higher, check EPP pod resources |

## Deployment

See the [Precise Prefix Cache-Aware Routing guide](https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-aware) for step-by-step deployment. For architecture details, see [KV-Cache Indexer architecture](/docs/architecture/advanced/kv-indexer).
