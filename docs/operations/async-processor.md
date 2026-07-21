# llm-d Async Processor Operations Guide

This guide covers operational best practices, scaling behavior, and container sizing recommendations for the [Async Processor](https://github.com/llm-d/llm-d-async).

The Async Processor is a **lightweight dispatch agent**, not an inference engine. It pulls requests from a message queue (Redis or GCP Pub/Sub), passes them through dispatch gates, and forwards them to the llm-d Router over HTTP. Because the heavy lifting (token generation) happens on the model servers, the processor is almost entirely **I/O-bound**: each worker spends the vast majority of its lifetime blocked on a long-running HTTP call to the Router. This shapes every sizing decision below — the container is small, and the constraint is concurrency and payload buffering, not compute.

> [!NOTE]
> The **concurrency** guidance in [§2](#2-choosing-concurrency-the-most-important-sizing-decision) is backed by the measured sweep in that section. The **CPU/memory** figures in [§3](#3-container-resource-sizing) are architecture-derived starting points — validate them against your own workload and prefer your own measurements where you have them.

---

## 1. Throughput Model

Understanding how the processor scales is a prerequisite to sizing it.

- **Workers are the unit of in-flight concurrency.** Each worker pulls one request, dispatches it, and blocks until the Router returns a result (or the deadline expires). The total number of requests the processor can have in flight at once is the sum of its worker counts.
- **The bottleneck is downstream, not the processor.** Dispatch rate is ultimately limited by inference-server capacity and the [dispatch gates](../architecture/advanced/batch/async-processor.md#dispatch-gates), not by the processor's CPU. Adding workers beyond what the inference pool can absorb only grows queue backlog and memory, not throughput.
- **Scaling is horizontal and stateless.** The processor is a pull-based consumer. Running N replicas against the same queue multiplies effective concurrency: `total in-flight = replicas × workers-per-replica`. Queue backends distribute messages across all consumers, so no leader election or coordination is required.

### Concurrency Configuration

In the helm chart, concurrency is controlled by `ap.concurrency` (default `64`), or per-pool via `ap.workerPools` when you need independent parallelism for different queues/topics:

```yaml
ap:
  concurrency: 64       # global worker count (default)
  # OR, for per-queue control:
  workerPools:
    - id: "high-priority"
      workers: 16
    - id: "bulk"
      workers: 8
```

When `workerPools` is set, the per-pool `workers` value overrides the global `concurrency` for that pool. Total in-flight concurrency for the replica is the sum of all pool worker counts.

---

## 2. Choosing Concurrency (the most important sizing decision)

Concurrency is the single highest-impact knob. The default of `64` is a sane starting point — enough to keep a modest pool busy — but it is **not** automatically right for yours: a single large GPU still has headroom above it, and high-throughput or long-output workloads need considerably more. The sizing rule is Little's Law:

```
required_workers ≈ target_throughput (req/s) × avg_request_latency (s)
```

Because LLM requests are slow (seconds to minutes end-to-end), the latency multiplier is large: with a 2 s average request, `concurrency: 64` caps you at `64 / 2 = 32 req/s`, no matter how much GPU sits behind the Router (the old default of `8` capped you at just `4 req/s`).

### Measured sweep

We ran a closed-loop sweep (each worker holds one in-flight request for its full duration — exactly an Async Processor worker) against a live llm-d stack: **Llama-3.1-8B on vLLM (`--max-num-seq 1024`), a single H100-80GB**, ~256-token outputs, dispatched through the inference gateway + EPP. Throughput and vLLM KV-cache utilization vs. concurrency:

| Concurrency | Throughput (req/s) | Mean latency (s) | GPU KV used | Notes |
| :--- | :--- | :--- | :--- | :--- |
| 8 | 4.2 | 1.9 | <1% | old default — GPU essentially idle |
| 32 | 14.7 | 2.2 | 3% | |
| **64 (default)** | **24.1** | 2.6 | 5% | current default |
| 128 | 35.8 | 3.6 | 12% | approaching saturation |
| 192 | 40.4 | 4.7 | 16% | **knee — best throughput/latency** |
| 256 | 44.3 | 5.8 | 20% | past knee, latency climbing |
| 512 | 61.4 | 9.7 | 51% | unstable: errors in repeated runs |

Takeaways:

- **The `64` default reaches ~60% of peak throughput.** At `concurrency: 64` this H100 sustains ~24 req/s (KV 5%) — a solid start, but still short of the ~40 req/s at the knee. The old default of `8` managed only ~4 req/s (KV <1%), roughly **10× below** the knee.
- **The knee for this backend is ~128–192 — about 2–3× the `64` default.** Beyond it, latency rises faster than throughput (256→512 doubled latency for ~40% more throughput).
- **Little's Law held exactly.** In-flight requests (`throughput × latency`) matched the worker count at every stable point — workers are never idle in the closed loop, so concurrency directly sets the load on the backend.
- **A single replica destabilized past ~256.** At very high concurrency a lone processor overwhelmed the gateway/EPP connection path (stalls and errors recurred). This is a reason to **scale out** rather than push one replica arbitrarily high (see [§4](#4-horizontal-scaling)).

### Applying it

1. Estimate (or measure) your workload's average end-to-end request latency `W` at the Router.
2. Set `concurrency ≈ ceil(target_throughput × W × 1.3)` (the 1.3 is headroom).
3. Cap per-replica concurrency at a level your gateway/EPP handles cleanly (≈128–256 in the test above) and add replicas beyond that.
4. **With a saturation/budget gate, you can set concurrency above this number and let the gate throttle dispatch** — but remember a gated worker still holds its pulled message in memory, so don't set it absurdly high.

Quick anchor (derived from the sweep, ~256-token outputs):

| Scenario | Target tput | ~Latency | Suggested concurrency | Topology |
| :--- | :--- | :--- | :--- | :--- |
| Single small GPU (dev) | ~30 req/s | ~3–5 s | **128** | 1 replica |
| Saturate 1× H100 (8B) | ~40 req/s | ~5 s | **~192** | 1 replica |
| Larger fleet | scale linearly | per workload | `tput × W × 1.3` | N replicas × ≤256 |

> [!NOTE]
> For long-output workloads, `W` grows proportionally (a 5k-token completion is ~10–20× a 256-token one), so the required concurrency grows with it — long-output pipelines often need concurrency in the high hundreds to low thousands, split across replicas.

---

## 3. Container Resource Sizing

The chart's container defaults are deliberately conservative — suitable for the default `concurrency: 64` with small payloads:

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

Scale these from the two variables that actually drive consumption: **worker concurrency** and **per-request payload size**.

### Memory Allocation

Memory is the dimension most likely to require tuning, and it scales predictably:

- **Per-request buffering dominates.** Each in-flight worker buffers its request body and the result payload in memory. A rough model:

  ```
  memory ≈ baseline (~128 MiB) + (concurrency × peak payload size × buffering factor ~2)
  ```

  The buffering factor accounts for serialized request + response held simultaneously, plus marshaling overhead.

- **Payload size, not worker count, is what forces memory up.** With small payloads (a few KiB), even high concurrency is cheap: at `concurrency: 192` and ~16 KiB payloads, payload memory is only `192 × 16 KiB × 2 ≈ 6 MiB` — the default `256Mi` is plenty. Large payloads flip this: a 100k-token prompt with a 5k-token completion is ~1 MiB per request, so `concurrency: 256` needs `128 MiB + 256 × 1 MiB × 2 ≈ 640 MiB`. **Raise memory when payloads are large, not merely when concurrency is high.**

- **Backlog does not accumulate in the processor.** Unlike the Router, undispatched requests stay in the message queue (Redis/Pub/Sub), not in processor memory. Only in-flight requests count toward the footprint, which is why memory tracks concurrency rather than queue depth.

### CPU Allocation

CPU consumption is low because workers are blocked on I/O most of the time. It scales with **dispatch rate**, not concurrency:

- **Per-dispatch cost** comes from JSON (de)serialization of request and result payloads, gate evaluation, and queue read/write. For typical agentic payloads, budget on the order of **0.5–1.0 vCPU per 100 dispatches/second**. Larger payloads raise the serialization cost proportionally.
- **Gate overhead.** The `prometheus-saturation` and `prometheus-budget` gates poll Prometheus on an interval; the cost is small and independent of dispatch rate. Setting `ap.prometheusCacheTTL` reduces query frequency when many workers share a gate. The `constant` and `redis` gates add negligible CPU.
- **Redis Sorted Set polling.** With the Redis backend, `pollIntervalMs` (default `1000`) and `batchSize` (default `10`) govern queue-read frequency. Aggressive (low-interval, small-batch) polling raises idle CPU and Redis load; tune `batchSize` up before lowering the interval.

### Sizing Reference (starting points)

Derived guidance for a single replica, by concurrency and payload profile. Treat as initial requests/limits to validate under load, not measured peaks.

| Workload profile | Concurrency | Payload (in/out) | CPU request | Memory request | Memory limit |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Default / light | 64 | small (≤4k/≤512) | 100m | 256Mi | 512Mi |
| Saturate 1 small GPU | 128–192 | small (≤4k/≤512) | 500m | 256Mi | 512Mi |
| Large agentic | 256 | large (100k/1k) | 1 | 1Gi | 2Gi |
| Long-output | 256 | large (100k/5k+) | 1 | 2Gi | 4Gi |

Note how the second row keeps the default memory despite 2–3× the concurrency — small payloads keep memory flat; only the large-payload rows need more.

Set the **memory limit close to the request** (the footprint is bounded by `concurrency × payload`, so it does not spike with backlog), and keep CPU limits generous relative to requests to absorb dispatch bursts without throttling.

---

## 4. Horizontal Scaling

Prefer horizontal scaling over a single large worker pool once a replica is comfortably sized — it improves availability and spreads queue-read and gate-polling load.

- **Increase replicas to raise total in-flight capacity** up to what the inference pool can absorb: `total in-flight = replicas × concurrency`. Beyond the inference pool's capacity, gates will hold requests closed and extra workers sit idle.
- **No coordination required.** Replicas independently pull from the shared queue. Redis Sorted Set and GCP Pub/Sub both distribute messages across consumers; Redis Pub/Sub fans out (each replica sees all messages), so use Sorted Set or Pub/Sub-per-consumer semantics when load should be *shared* rather than duplicated.
- **Gates remain effective across replicas.** Prometheus-based gates observe global inference-server saturation, so independent replicas converge on the same dispatch decisions without sharing state.
- **Graceful shutdown.** The processor drains in-flight work on termination (`drainTimeout`, default `2m`). Ensure your pod `terminationGracePeriodSeconds` is at least `drainTimeout` so rolling updates and scale-down do not abandon in-flight requests.

### Helm Resource Override Example

Example values overriding concurrency and container resources for a large-agentic profile (100k/1k payloads) at higher concurrency:

```yaml
ap:
  concurrency: 256
  resources:
    requests:
      cpu: "1"
      memory: "1Gi"
    limits:
      cpu: "2"
      memory: "2Gi"
```

```bash
helm install async-processor \
  oci://ghcr.io/llm-d/charts/async-processor \
  -f guides/asynchronous-processing/${MQ_PROVIDER}/values.yaml \
  -f resource_overrides.yaml \
  --set ap.igwBaseURL=http://${IP}:80 \
  -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}
```

---

## Related

- [Asynchronous Processing Well-Lit Path](../well-lit-paths/workloads/batch-serving/asynchronous-processing.md) — overview and use cases.
- [Asynchronous Processing Guide](../../guides/asynchronous-processing/README.md) — deployment instructions for Redis and GCP Pub/Sub.
- [Async Processor Architecture](../architecture/advanced/batch/async-processor.md) — internal mechanics, gates, and queue integrations.
- [llm-d Router Operations Guide](router.md) — sizing for the Router/EPP and standalone proxy.
