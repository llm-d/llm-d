# Latency Predictor

The Latency Predictor is the llm-d component behind predicted latency-based scheduling. Instead of scoring pods by coarse utilization signals alone, the EPP asks an online-trained ML model to predict **Time To First Token (TTFT)** and **Time Per Output Token (TPOT)** for each candidate pod, then routes on those predictions — optionally gated by per-request Service Level Objectives (SLOs).

This page is a reference for the component: its design, EPP plugins, ML model, failure modes, and scaling characteristics. For step-by-step adoption — Helm enablement, SLO header usage, verification, troubleshooting — see the [Predicted Latency well-lit path](../../well-lit-paths/predicted-latency.md). Design rationale and benchmarks are in the blog [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms).

## Why Predicted Latency?

Utilization-based load balancing has structural gaps for LLM workloads:

- **Request costs vary enormously.** A 10-token prompt and a 10,000-token prompt both count as one queued request, but their prefill and decode costs differ by orders of magnitude.
- **Cache reuse is unstable.** Prefix hit rates shift as traffic patterns change, so static weights between cache affinity and utilization drift out of tune.
- **Conflicting objectives.** Optimizing TPOT favors spreading load; optimizing TTFT and cache reuse favors consolidating it. No fixed weight configuration is correct in all regimes.
- **No SLO awareness.** Utilization scorers cannot answer "can this pod serve this request within the caller's latency budget?"

The Latency Predictor closes those gaps by learning the mapping from `(pod state, request features) → latency` directly from live traffic, and letting the EPP reason about headroom against SLOs instead of hand-tuned weights.

## Architecture

The predictor ships as a set of sidecars colocated with the EPP in the same pod. A **training server** continuously retrains on completed requests, and one or more **prediction servers** that read the latest model from a shared volume and answer predictions from the EPP on the hot path.

```
                ┌──────────────────────────────────────────────────────┐
                │                      EPP Pod                         │
                │                                                      │
                │  ┌─────────────┐        ┌──────────────────────────┐ │
                │  │             │        │   Training Server        │ │
 Inference ────►│  │     EPP     │───────►│                          │ │
 Requests       │  │             │samples │   Collects completed     │ │
                │  │ latency-    │        │   requests, retrains     │ │
                │  │ based       │        │   XGBoost models         │ │
                │  │ plugins     │        └────────────┬─────────────┘ │
                │  │             │                     │ writes        │
                │  │             │                     ▼ models        │
                │  │             │        ┌──────────────────────────┐ │
                │  │             │        │   Shared Volume          │ │
                │  │             │        └────────────┬─────────────┘ │
                │  │             │                     │ reads         │
                │  │             │                     ▼ models        │
                │  │             │        ┌──────────────────────────┐ │
                │  │             │───────►│   Prediction Servers     │ │
                │  │             │predict │   (horizontally scaled)  │ │
                │  │             │◄───────│                          │ │
                │  │             │result  │   Serve TTFT/TPOT        │ │
                │  │             │        │   predictions            │ │
                │  └─────────────┘        └──────────────────────────┘ │
                └──────────────────────────────────────────────────────┘
```

In the EPP, latency-based scheduling is implemented as a series of composable EPP plugins (more on this later). The `predicted-latency-producer` plugin drives interactions with the predictor. For each request, it calls the predictor to obtain TTFT and TPOT predictions for every candidate endpoint, conditioned on the endpoint's state (KV cache utilization, queue depth, prefix cache match score). After the request is served, the producer sends the observed TTFT and ITL latencies back to the predictor as training samples, so the model is continuously retrained on live traffic.

If the prediction server is unreachable or fails to return a prediction, the latency scorer falls back to a composite score built from KV cache utilization, queue depth, and prefix cache match — so a predictor outage degrades to baseline heuristic routing rather than dropping traffic.

### ML Model

The prediction model is an **XGBoost** regression one trained in realtime. Two models are maintained — one for TTFT, one for TPOT — and retrained on a sliding window of completed requests.

Training uses **stratified bucketing** — samples are partitioned by KV cache utilization (10% steps), prefix cache hit rate (0.25 steps), and similar features. Each bucket has its own sample cap, so traffic regimes that are rare in the current window (for example, a cold prefix cache during low load) are not forgotten by the model. Across benchmark runs the models achieve approximately **5% Mean Absolute Percentage Error** on both targets.

The predictor assumes a **homogeneous inference pool** — every pod in the pool must share the same GPU type, model weights, and serving configuration. The features the model is trained on describe pod state (KV cache utilization, queue depth, running requests, prefix hit rate) without encoding pod shape, so predictions across heterogeneous hardware or serving configs would conflate regimes the model treats as identical. Heterogeneous pools are not yet modeled.

**TTFT features**

| Feature | What it captures |
|---------|------------------|
| KV Cache Usage % | Memory saturation and its effect on prefill scheduling |
| Input Length | Prefill cost proxy — longer prompts dominate TTFT |
| Queue Depth | Backlog before scheduling — more waiting requests delay first token |
| Running Requests | GPU concurrency — contention with in-flight decode |
| Prefix Cache Match % | KV reuse potential — high match rates collapse prefill work |
| Input Tokens In Flight | Tokens dispatched but not yet prefilled, plus tokens still resident in KV — captures incoming prefill pressure |

**TPOT features**

| Feature | What it captures |
|---------|------------------|
| KV Cache Usage % | Memory pressure during decode |
| Input Length | Input token count (affects attention cost) |
| Queue Depth | Queue contention that leaks into decode batching |
| Running Requests | Active decode batch size / GPU concurrency |
| Tokens Generated | Output tokens produced so far |

### Scalability

Each prediction sidecar sustains roughly 300 QPS of prediction work on a `c4-standard-192` node (~192 vCPUs). Because the EPP makes one prediction per candidate pod, total prediction load scales with `cluster QPS × pod count`. Scale horizontally by adding prediction sidecars and updating the predictor URL list.

| Cluster QPS | Avg prediction latency (ms)  | p99 prediction latency (ms)  | Prediction servers  |
|-------------|------------------------------|------------------------------|---------------------|
| 100         | 3.5                          | 46                           | 1                   |
| 1,000       | 5.0                          | 49                           | 2                   |
| 5,000       | ~27                          | ~74                          | 2                   |
| 7,500       | ~35                          | ~96                          | 3                   |
| 10,000      | ~48                          | ~137                         | 4                   |

### Streaming Mode

The `predicted-latency-producer` plugin has two training modes, exposed via a `streamingMode` parameter:

- **`streamingMode: false`** (default) — Trains on end-to-end request latency. TTFT is recorded at response completion (effectively e2e latency); TPOT is not trained. **Use this mode if** your workload mixes streaming and non-streaming responses, or if you only need latency-aware routing without per-request SLO enforcement.
- **`streamingMode: true`** — Trains separate TTFT and TPOT models. TTFT is recorded on the first streamed chunk; TPOT is sampled across subsequent tokens. **Use this mode if** your workload is fully streaming and you need meaningful `x-slo-ttft-ms` / `x-slo-tpot-ms` enforcement — a mixed workload in this mode will produce incorrect measurements for the non-streamed responses.

## Scheduling Strategy

As indicated before, latency-based scheduling is implemented in the EPP as a set of composable plugins. Using the latency predictions obtained by `predicted-latency-producer` plugin, scheduling is done using a sequence of filtering and scoring plugins to determine the optimal placement. When the latency predictor is enabled via the Helm chart, the full sequence is wired up automatically. SLO-specific plugins are no-ops when a request does not include SLO headers, so the same set of plugins handles both SLO and non-SLO annotated traffic.

### Filtering Strategy

Two filters narrow the candidate set before scoring.

**`prefix-cache-affinity-filter`** narrows the candidate set to cache-warm endpoints when any endpoint's prefix cache match score exceeds the affinity threshold (default `0.80`). If no endpoint clears the threshold, the filter is a no-op. The filter implements an epsilon-greedy exploit/explore over cache locality, with a TTFT-based escape hatch:

- **Exploit** (default path). The filter narrows to cache-warm endpoints so downstream scoring concentrates reuse on them.
- **Explore** (small probability). On a configurable fraction of requests the filter bypasses itself entirely, letting traffic land on cache-cold pods so they can seed new entries — this prevents a single pod from permanently owning a hot prefix.
- **TTFT load gate**. Even on the exploit path, if the best cache-warm pod's predicted TTFT is materially worse than the best overall pod's (by more than a configurable penalty), the filter breaks affinity and yields the full candidate set. This stops a hot prefix from piling up behind a saturated pod while cooler pods sit idle.

**`slo-headroom-tier-filter`** splits endpoints into a **positive** tier (predicted to meet SLO) and a **negative** tier (predicted to violate SLO), with probabilistic exploration of the negative tier so recovering pods still receive traffic. No-op when SLO headers are absent.

### Scoring Strategy

Three plugins handle scoring and final selection.

**`latency-scorer`** scores endpoints. Without SLO headers, lowest predicted latency wins. With SLO headers, the score is derived from headroom (`SLO − predicted`) via the `headroomSelectionStrategy` parameter:

- **`least`** (default). Bin-pack: prefer the endpoint closest to the SLO boundary — smallest positive headroom if any pod meets the SLO, smallest negative deficit otherwise. Maximizes utilization and keeps less-loaded pods free for bursty arrivals.
- **`most`**. Spread: prefer the endpoint with the most positive headroom. More conservative, leaves slack for unexpected spikes. For negative headroom, `least` is always used regardless of this setting.

**`latency-slo-admitter`** rejects *sheddable* requests (priority < 0) when no endpoint can meet the SLO, rather than wasting capacity on a guaranteed miss. No-op when SLO headers are absent.

**`weighted-random-picker`** selects an endpoint via weighted random selection over the scores. This spreads load while still favoring better-scoring endpoints, and avoids the "everyone piles onto the current best pod" failure mode of pure arg-max selection.

## Observability

When the latency predictor is enabled, the EPP exposes Prometheus metrics for actual vs. predicted latency, prediction duration, and SLO violation tracking. The primary series are:

| Metric | Description |
|--------|-------------|
| `inference_objective_request_ttft_seconds` | Actual TTFT distribution, per model / target model. |
| `inference_objective_request_predicted_ttft_seconds` | Predicted TTFT distribution, per model / target model. |
| `inference_objective_request_ttft_prediction_duration_seconds` | Time spent generating TTFT predictions. |
| `inference_objective_request_tpot_seconds` | Actual TPOT distribution. |
| `inference_objective_request_predicted_tpot_seconds` | Predicted TPOT distribution. |
| `inference_objective_request_tpot_prediction_duration_seconds` | Time spent generating TPOT predictions. |
| `inference_objective_request_ttft_slo_violation_total` | Counter of TTFT SLO violations. |
| `inference_objective_request_tpot_slo_violation_total` | Counter of TPOT SLO violations. |

All latency and prediction-duration series are Prometheus **histograms**, so dashboards should query them via `histogram_quantile` (and the counters via `rate`) rather than reading instantaneous values. Pairing actuals with predictions lets operators validate predictor accuracy in-situ; SLO violation counters are the primary signal for alerting on SLO breaches.

## Source

- **EPP plugins** (`predicted-latency-producer`, `prefix-cache-affinity-filter`, `latency-scorer`, `weighted-random-picker`, `slo-headroom-tier-filter`, `latency-slo-admitter`) live in [llm-d Router](https://github.com/llm-d/llm-d-inference-scheduler). Per-plugin configuration references live alongside each plugin in that repo.
- **Training and prediction server code** (the Python ML sidecars, XGBoost models, stratified sampler) lives in [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor).

## Further Reading

- [Predicted Latency Well-Lit Path](../../well-lit-paths/predicted-latency.md) — how to adopt this path: Helm enablement, request headers, verification, troubleshooting.
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms) — design rationale and benchmark results.
- [EPP Scheduling](../core/router/epp/scheduling.md) — how the plugins fits into EPP request handling.
