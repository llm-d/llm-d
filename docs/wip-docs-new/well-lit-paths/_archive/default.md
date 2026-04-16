# Load-Aware and Approximate Prefix Cache Scheduling

The recommended starting point for any llm-d deployment. Reduces tail latency and increases throughput through load-aware and approximate prefix-cache-aware routing -- no additional infrastructure beyond the EPP itself.

## What It Does

Three scoring signals are combined to select the best endpoint for each request:

| Scorer | Weight | What It Prevents |
|---|---|---|
| `queue-depth-scorer` | 2 | Overloading a single server while others sit idle |
| `kv-cache-utilization-scorer` | 2 | Routing to servers about to evict cached prefixes |
| `prefix-cache-scorer` | 1 | Ignoring cache locality, forcing unnecessary recomputation |

The 2:2:1 weight ratio balances load awareness against cache affinity. Without load awareness, a server holding a popular prefix receives all matching requests until it saturates -- evicting the very prefix that attracted traffic.

## Configuration

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
    picker:
      pickerName: max-score-picker
```

## Progressive Tuning

Start with the default 2:2:1 weights. After observing traffic for a day, adjust based on what you see:

| Observation | Change | Why |
|---|---|---|
| Cache hit ratio >80% but TTFT still high | Increase `prefix-cache-scorer` to 2 or 3 | Give cache affinity more pull -- hits are valuable but underweighted |
| Cache-miss requests oscillating between same pods | Add `no-hit-lru-scorer` with weight 1 | Distributes cold requests more evenly across the pool |
| Uneven queue depths across pods | Increase `queue-depth-scorer` to 3 | Prioritize load balance over cache locality |
| KV-cache usage low everywhere (&lt;50%) | Lower `kv-cache-utilization-scorer` to 1 | Cache eviction is unlikely -- spend the scoring budget elsewhere |

Monitor `inference_extension_prefix_indexer_hit_ratio` and `vllm:kv_cache_usage_perc` to guide these decisions.

## When To Upgrade

| Symptom | Upgrade To |
|---|---|
| High prefix sharing but approximate scorer misroutes | [Precise Prefix Cache-Aware Routing](precise-prefix-cache-aware-routing) |
| Need per-request SLO guarantees | [Predicted Latency Scheduling](predicted-latency) |
| Multi-tenant traffic needs priority/fairness | [Flow Control](flow-control) |

## Deployment

Default scheduling is the out-of-the-box configuration -- no additional setup beyond the standard llm-d deployment. See the [deployment guide](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling).
