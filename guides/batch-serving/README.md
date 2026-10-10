# Batch Serving in llm-d

The **batch-serving** workload umbrella provides recommended, cohesive deployments for processing large-scale, offline, or latency-insensitive tasks on llm-d infrastructure.

Serving batch and offline inference workloads alongside real-time, interactive traffic presents distinct operational challenges:

- **Resource Utilization**: Interactive traffic is bursty and leaves GPU/TPU capacity underutilized during off-peak hours ("slack capacity").
- **Traffic Isolation & SLAs**: Uncontrolled batch job dispatching can cause queue contention, high TTFT (Time to First Token), and degraded ITL (Inter-Token Latency) for online users.
- **API & Protocol Compatibility**: Multi-tenant platforms often require an OpenAI-compatible Batch API (`/v1/batches`, `/v1/files`) with file management, while internal backend pipelines benefit from lightweight message queues.

`llm-d` addresses these challenges by offering two complementary paths for batch and asynchronous processing that integrate directly with the llm-d Router:

1. **[Batch Gateway](./batch-gateway/README.md)**: An enterprise-grade, fully managed **OpenAI-compatible Batch API** for formal job submission, file storage, status tracking, and multi-tenant batch management.
2. **[Asynchronous Processing](./asynchronous-processing/README.md)**: A lightweight, queue-based dispatch mechanism (using Redis Sorted Sets or GCP Pub/Sub) featuring **dynamic dispatch gating** based on live model server saturation metrics (KV cache pressure, queue depth).

For the broader architectural context and design principles, see the [Batch Architecture documentation](../../docs/architecture/advanced/batch/README.md).

---

## Guide Index

- **[Batch Gateway Guide](./batch-gateway/README.md)**: Deploy an OpenAI-compatible batch API (`/v1/batches`, `/v1/files`) with pluggable metadata storage (PostgreSQL/Redis), file storage (S3/RWX PVC), and a batch processor that dispatches requests to the llm-d Router.
- **[Asynchronous Processing Guide](./asynchronous-processing/README.md)**: Deploy the lightweight Async Processor to consume requests from message queues with metric-based dispatch gating.
  - **[GCP Pub/Sub Backend](./asynchronous-processing/gcp-pubsub/README.md)**: Configure Async Processor with Google Cloud Pub/Sub.
  - **[Redis Sorted Set Backend](./asynchronous-processing/redis/README.md)**: Configure Async Processor with Redis / Valkey.
  - **[Multi-Tenant Async Processing](./asynchronous-processing/multitenant/README.md)**: Advanced multi-tenant setup with team quotas, tier-priority dispatch, and saturation back-off across inference pools.

---

## Understanding the Two Approaches

### 1. Batch Gateway (Job-Oriented Batching)

The **Batch Gateway** provides a standard REST API with full schema parity for OpenAI's `/v1/batches` and `/v1/files` endpoints. It is designed for workflows where users or client applications submit batch files and poll or wait for completed results.

- **Key Components**:
  - **API Server**: Handles file uploads, validates JSONL request payloads, and tracks batch job state.
  - **Batch Processor**: Dequeues jobs, coordinates model routing, streams individual requests to the llm-d Router, and writes output files.
  - **Garbage Collector**: Manages retention policies and cleans up expired files and batch artifacts.
- **Workflow**:
  1. Client uploads a `.jsonl` file containing batch inference requests to `/v1/files`.
  2. Client creates a batch job via `POST /v1/batches` referencing the uploaded file.
  3. Batch Processor executes the requests against the llm-d Router and writes the output file.
  4. Client polls status and downloads the result file upon completion.

### 2. Async Processor (Queue-Based Stream Dispatch)

The **Async Processor** is a lightweight, high-throughput agent designed to decouple request submission from inference execution using standard message queues.

- **Key Capabilities**:
  - **Dynamic Dispatch Gating**: Evaluates downstream engine telemetry (such as KV cache utilization and request queue depth via Prometheus) to dispatch background requests only when slack capacity is available, protecting interactive traffic from latency spikes.
  - **Quota & Priority Management**: Enforces concurrency limits, budget gates, and tier-based scheduling across multiple tenants and worker pools.
  - **Resilience**: Automatically retries transient failures with exponential backoff until the request's deadline; dead-lettering, where available, is the broker's (GCP Pub/Sub subscriptions).

### 3. Unified Hybrid Deployment

Batch Gateway and Async Processor can be deployed together. In a composite deployment, the Batch Gateway decomposes large batch jobs and publishes individual requests into the Async Processor's queue, combining OpenAI API job tracking with fine-grained Prometheus-based dispatch gating.

---

## Comparative Analysis

| Dimension | Batch Gateway | Async Processor |
| :--- | :--- | :--- |
| **Primary Interface** | OpenAI-compatible REST API (`/v1/batches`, `/v1/files`) | Message Queue (Redis Sorted Set, GCP Pub/Sub) |
| **Unit of Work** | Batch job containing up to 50,000+ requests in a JSONL file | Individual messages / streaming request queue |
| **Flow Control** | Downstream rate-limiting and router integration | Dynamic dispatch gating (Prometheus saturation, KV-cache pressure, budget) |
| **State & Storage** | Database (PostgreSQL/Redis) + Object/File Storage (S3 / RWX PVC) | Message broker (Redis / Pub/Sub) |
| **Multi-Tenancy** | Tenant-scoped jobs, files, and header pass-through authentication | Worker pools, priority tiers, and per-team quota allocation |
| **Client Interaction** | File upload &rarr; Job creation &rarr; Polling &rarr; Result download | Publish message to queue &rarr; Listen on result topic/queue |
| **Deployment Complexity** | Moderate (API server, processor, GC, database, shared storage) | Low (Async processor daemon + message queue) |
| **Typical Use Cases** | Offline evaluations, daily dataset scoring, multi-tenant batch service | Background task processing, microservice pipelines, slack capacity filling |

---

## When to Choose Which?

### Choose Batch Gateway if

- Your clients expect an **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`) for easy integration with standard SDKs.
- You need **job-level tracking**, status queries, progress reporting, and output file management.
- You operate a multi-tenant platform requiring formal job submission, file storage, and authentication pass-through.
- You are running offline model evaluations, bulk synthetic data generation, or dataset enrichment pipelines.

### Choose Async Processor if

- You need lightweight asynchronous inference for **internal microservices or event-driven pipelines**.
- You want to **harvest slack capacity** in your interactive inference pools without impacting real-time SLOs using Prometheus-driven dispatch gating.
- Your infrastructure already uses message brokers like Redis or GCP Pub/Sub.
- You require **fine-grained rate limiting, priority queuing, and per-tenant quota tiers** across shared model servers.

---

## Centralized Configuration & Dependencies

Both batch solutions dispatch inference requests to an existing llm-d serving stack. Before deploying:

1. **Deploy the Inference Stack**: Ensure you have a running model server and llm-d Router deployed via the [Optimized Baseline](../optimized-baseline/README.md) or related workload guides.
2. **Configure Environment Variables**: Source [`guides/env.sh`](../env.sh) for shared environment variables and Helm repository configurations.
3. **Review Operations Guidance**: For sizing, scaling, and production deployment patterns of the Async Processor, see [Async Processor Operations](../../docs/operations/components/async-processor.md).

---

## Observability & Troubleshooting

Once monitoring is enabled (see [Observability setup](../../docs/operations/observability/setup.md)), use the signals below to operate batch serving. This section covers what is specific to this path. Batch Gateway metric definitions and alerts are in the shared [metric reference](../../docs/operations/observability/batch-gateway-metrics.md) and [alerting rules](../../docs/operations/observability/alerting.md#batch-gateway-batch-gatewayrules). The Async Processor's full metric list is in the [llm-d-async metrics reference](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics).

Both approaches put a queue in front of the inference pool, so most problems show up as a backlog. The operator question is always the same: is the backlog there because the model servers are saturated, or because the batch layer itself is not dispatching?

### Async Processor

Async Processor metrics are registered under the `llm_d_async` subsystem, so the exposed names carry a doubled prefix (`llm_d_async_async_*`). Per-queue series carry `queue_id`, `queue_name` and `pool_name`.

#### Key metrics for this path

| Signal | Why it matters for batch serving | Where to look |
| ------ | -------------------------------- | ------------- |
| Broker backlog (`llm_d_async_async_broker_backlog`) | Work waiting in Redis or Pub/Sub that the processor has not pulled yet. A zero is only trustworthy when `llm_d_async_async_broker_backlog_source_available` is `1` | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |
| In-process queue (`llm_d_async_async_queue_depth`) and queue time (`llm_d_async_async_queue_residence_time_millis`) | Requests already pulled from the broker and waiting for a worker. This is the delay the async layer itself adds | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |
| Worker utilization (`sum by (pool_name) (llm_d_async_async_inflight_requests) / llm_d_async_async_pool_worker_limit`) | Near 1.0 means the worker limit, not the model servers, caps throughput. Inflight requests are per-queue, so aggregate to pool first; the limit is already per-pool | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |
| Dispatch budget (`llm_d_async_async_dispatch_budget`) and gate decisions (`llm_d_async_async_gate_decisions_total` by `reason`) | The gate deliberately holds work back when the pool is busy. A budget of 0 with `gate_closed` decisions is the gate doing its job; `error` decisions are not | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |
| Gate input (`llm_d_async_async_gate_metric_value` vs `llm_d_async_async_gate_metric_threshold`, with `llm_d_async_async_gate_metric_source_available`) | The budget the gate last computed from its source, compared against the threshold; the gate closes at or below it. When the source is unavailable the gate falls back to its configured default | [Asynchronous processing guide](./asynchronous-processing/README.md) |
| Inference time (`llm_d_async_async_inference_latency_time_millis`) | Time spent in the router and model servers, measured per attempt. Read it against queue time to see which side the delay is on | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |
| Outcomes (`llm_d_async_async_successful_requests_total`, `_failed_requests_total`, `_shedded_requests_total`, `_exceeded_deadline_requests_total`, `_request_retries_total`) | Shed requests were refused with HTTP 429; deadline-exceeded requests aged out before finishing. 5xx errors from the router or model servers drive retries and failures instead of shedding | [llm-d-async metrics](https://github.com/llm-d/llm-d-async/blob/main/README.md#prometheus-metrics) |

#### Common failure modes

- **Backlog grows while workers sit idle**: utilization is well below 1.0 and `llm_d_async_async_dispatch_budget` is at or near 0. The gate is closed when `gate_metric_value` ≤ `gate_metric_threshold`. The value is the gate's budget reading (1 − saturation for saturation gates), not the raw pool metric, so a low value means the pool really is saturated and the backlog is expected, since this path fills slack capacity. If `gate_metric_source_available` is 0, the gate cannot read its source (for example Prometheus is unreachable) and is running on its default; fix the source before tuning thresholds.
- **Backlog grows with utilization pinned at 1.0**: the worker limit is the bottleneck. If the pool still has headroom (low `llm_d_epp_flow_control_pool_saturation` or `vllm:num_requests_running`), raise the pool's worker limit.
- **High queue time, normal inference time**: requests wait inside the processor, not in the model servers. Check worker utilization and the gate before scaling the pool.
- **Rising inference time with the gate open**: the model servers are the slow side. Diagnose them with the router and vLLM signals in the [metric reference](../../docs/operations/observability/metrics.md).
- **Deadline-exceeded requests climbing**: the drain rate cannot meet the requested deadlines. On the `redis-sortedset` broker, `llm_d_async_async_deadline_proximity_millis` shows how close queued items are to their deadlines before they expire. It is a per-poll snapshot, so read it with `histogram_quantile` rather than `rate()`.
- **Shed requests climbing**: the router or model servers are refusing requests with HTTP 429 (over capacity). 5xx errors instead drive retries and failures: watch `_request_retries_total` and `_failed_requests_total`.

### Batch Gateway

Batch Gateway metric names carry no prefix (`jobs_processed_total`, `active_workers`), so scope every query by `namespace`, as the bundled alert rules do.

#### Key metrics for this path

| Signal | Why it matters for batch serving | Where to look |
| ------ | -------------------------------- | ------------- |
| Queue wait (`job_queue_wait_duration_seconds`) | Time a job spends in the priority queue before a worker picks it up. There is no live queue-depth gauge, so this is the leading indicator of backlog | [Metrics → Batch Gateway](../../docs/operations/observability/batch-gateway-metrics.md) |
| Worker saturation (`active_workers` / `total_workers`) | Sustained near 1.0 means the processor's worker pool is the bottleneck | [Metrics → Batch Gateway](../../docs/operations/observability/batch-gateway-metrics.md) |
| Job outcomes (`jobs_processed_total` by `result` and `reason`) | `expired` means jobs aged out before running, which is a capacity problem; `re_enqueued` counts jobs sent back to the queue | [Metrics → Batch Gateway](../../docs/operations/observability/batch-gateway-metrics.md) |
| Backpressure (`batch_processor_aimd_concurrency_limit`, `batch_processor_aimd_decreases_total` by `signal`) | The processor lowers its per-endpoint concurrency when the backend answers with `429`, `5xx` or a capacity retry. A falling limit means the inference pool is pushing back | [llm-d-batch-gateway metrics](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/metrics.md) |
| Per-model load and errors (`model_inflight_requests`, `request_errors_by_model_total`) | Isolates one misbehaving model in a multi-model deployment | [llm-d-batch-gateway metrics](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/metrics.md) |
| File storage (`file_storage_operations_total` by `status`) | `status="exhausted"` means retries gave up, so input or output files are unreachable | [Metrics → Batch Gateway](../../docs/operations/observability/batch-gateway-metrics.md) |

#### Common failure modes

- **Queue wait rising with workers saturated**: the worker pool is too small for the submission rate (`BatchGatewayWorkersSaturated`, `BatchGatewayHighQueueWait`). Raise `num_workers` (processor config) / `processor.config.numWorkers` (Helm chart) or add processor replicas, as long as the inference pool has headroom.
- **Queue wait rising with workers not saturated and the AIMD limit falling**: the backend is pushing back. `batch_processor_aimd_decreases_total` by `signal` shows whether it is 429s (capacity) or 5xx (errors). Adding workers will not help; the fix is on the inference side.
- **Expired jobs** (`BatchGatewayExpiredJobsDetected`): jobs aged out before execution. Treat it as a capacity or completion-window problem, not a job failure.
- **Failed jobs** (`BatchGatewayHighJobFailureRate`): check `request_errors_by_model_total` to see whether one model accounts for them, and `file_storage_operations_total{status="exhausted"}` for jobs that failed on input or output storage.
- **Orphaned jobs**: a non-zero `batch_reconciler_orphans_recovered_total` points at processor crashes, and `BatchReconcilerErrors` means the reconciler itself is failing to recover them.

llm-d's bundled alerting rules cover the Batch Gateway only; the Async Processor's alerts (`AsyncProcessorHighRetryRate`, `AsyncProcessorHighDeadlineExceededRate`, `AsyncProcessorLowSuccessRate`, `AsyncProcessorHighShedRate`) ship in the llm-d-async chart's PrometheusRule.

## Related Resources

- [Batch Architecture Overview](../../docs/architecture/advanced/batch/README.md)
- [Async Processor Architecture](../../docs/architecture/advanced/batch/async-processor.md)
- [Batch Gateway Architecture](../../docs/architecture/advanced/batch/batch-gateway.md)
- [llm-d-async Repository](https://github.com/llm-d/llm-d-async)
- [llm-d-batch-gateway Repository](https://github.com/llm-d/llm-d-batch-gateway)
- [SIG Batch Inference](../../SIGS.md#sig-batch-inference)
