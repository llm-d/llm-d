# Batch Serving in llm-d

The **batch-serving** workload umbrella provides recommended, cohesive deployments for processing large-scale, offline, or latency-insensitive tasks on llm-d infrastructure.

Serving batch and offline inference workloads alongside real-time, interactive traffic presents distinct operational challenges:
- **Resource Utilization**: Interactive traffic is bursty and leaves GPU/TPU capacity underutilized during off-peak hours ("slack capacity").
- **Traffic Isolation & SLAs**: Uncontrolled batch job dispatching can cause queue contention, high TTFT (Time to First Token), and degraded ITL (Inter-Token Latency) for online users.
- **API & Protocol Compatibility**: Multi-tenant platforms often require an OpenAI-compatible Batch API (`/v1/batches`, `/v1/files`) with file management, while internal backend pipelines benefit from lightweight message queues.

`llm-d` addresses these challenges by offering two complementary paths for batch and asynchronous processing that integrate directly with the llm-d Router:

1. **[Batch Gateway](./batch-gateway/README.md)**: An enterprise-grade, fully managed **OpenAI-compatible Batch API** for formal job submission, file storage, status tracking, and multi-tenant batch management.
2. **[Asynchronous Processing](./asynchronous-processing/README.md)**: A lightweight, queue-based dispatch mechanism (using Redis Sorted Sets or GCP Pub/Sub) featuring **dynamic dispatch gating** based on live model server saturation metrics (KV cache pressure, queue depth).

For the broader architectural context and design principles, see the [Batch Serving workload overview](../../docs/well-lit-paths/workloads/batch-serving/README.md) and [Batch Architecture documentation](../../docs/architecture/advanced/batch/README.md).

---

## Guide Index

* **[Batch Gateway Guide](./batch-gateway/README.md)**: Deploy an OpenAI-compatible batch API (`/v1/batches`, `/v1/files`) with pluggable metadata storage (PostgreSQL/Redis), file storage (S3/RWX PVC), and a batch processor that dispatches requests to the llm-d Router.
* **[Asynchronous Processing Guide](./asynchronous-processing/README.md)**: Deploy the lightweight Async Processor to consume requests from message queues with metric-based dispatch gating.
  * **[GCP Pub/Sub Backend](./asynchronous-processing/gcp-pubsub/README.md)**: Configure Async Processor with Google Cloud Pub/Sub.
  * **[Redis Sorted Set Backend](./asynchronous-processing/redis/README.md)**: Configure Async Processor with Redis / Valkey.
  * **[Multi-Tenant Async Processing](./asynchronous-processing/multitenant/README.md)**: Advanced multi-tenant setup with team quotas, tier-priority dispatch, and saturation back-off across inference pools.

---

## Understanding the Two Approaches

### 1. Batch Gateway (Job-Oriented Batching)

The **Batch Gateway** provides a standard REST API with full schema parity for OpenAI's `/v1/batches` and `/v1/files` endpoints. It is designed for workflows where users or client applications submit batch files and poll or wait for completed results.

* **Key Components**:
  * **API Server**: Handles file uploads, validates JSONL request payloads, and tracks batch job state.
  * **Batch Processor**: Dequeues jobs, coordinates model routing, streams individual requests to the llm-d Router, and writes output files.
  * **Garbage Collector**: Manages retention policies and cleans up expired files and batch artifacts.
* **Workflow**:
  1. Client uploads a `.jsonl` file containing batch inference requests to `/v1/files`.
  2. Client creates a batch job via `POST /v1/batches` referencing the uploaded file.
  3. Batch Processor executes the requests against the llm-d Router and writes the output file.
  4. Client polls status and downloads the result file upon completion.

### 2. Async Processor (Queue-Based Stream Dispatch)

The **Async Processor** is a lightweight, high-throughput agent designed to decouple request submission from inference execution using standard message queues.

* **Key Capabilities**:
  * **Dynamic Dispatch Gating**: Evaluates downstream engine telemetry (such as KV cache utilization and request queue depth via Prometheus) to dispatch background requests only when slack capacity is available, protecting interactive traffic from latency spikes.
  * **Quota & Priority Management**: Enforces concurrency limits, budget gates, and tier-based scheduling across multiple tenants and worker pools.
  * **Resilience**: Automatically retries transient failures with exponential backoff and dead-letter handling.

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

### Choose Batch Gateway if:
* Your clients expect an **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`) for easy integration with standard SDKs.
* You need **job-level tracking**, status queries, progress reporting, and output file management.
* You operate a multi-tenant platform requiring formal job submission, file storage, and authentication pass-through.
* You are running offline model evaluations, bulk synthetic data generation, or dataset enrichment pipelines.

### Choose Async Processor if:
* You need lightweight asynchronous inference for **internal microservices or event-driven pipelines**.
* You want to **harvest slack capacity** in your interactive inference pools without impacting real-time SLOs using Prometheus-driven dispatch gating.
* Your infrastructure already uses message brokers like Redis or GCP Pub/Sub.
* You require **fine-grained rate limiting, priority queuing, and per-tenant quota tiers** across shared model servers.

---

## Centralized Configuration & Dependencies

Both batch solutions dispatch inference requests to an existing llm-d serving stack. Before deploying:

1. **Deploy the Inference Stack**: Ensure you have a running model server and llm-d Router deployed via the [Optimized Baseline](../optimized-baseline/README.md) or related workload guides.
2. **Configure Environment Variables**: Source [`guides/env.sh`](../env.sh) for shared environment variables and Helm repository configurations.
3. **Review Operations Guidance**: For sizing, scaling, and production deployment patterns of the Async Processor, see the [Async Processor Operations Guide](../../docs/operations/async-processor.md).

---

## Related Resources

* [Batch Serving Workload Narrative](../../docs/well-lit-paths/workloads/batch-serving/README.md)
* [Batch Architecture Overview](../../docs/architecture/advanced/batch/README.md)
* [Async Processor Architecture](../../docs/architecture/advanced/batch/async-processor.md)
* [Batch Gateway Architecture](../../docs/architecture/advanced/batch/batch-gateway.md)
* [llm-d-async Repository](https://github.com/llm-d/llm-d-async)
* [llm-d-batch-gateway Repository](https://github.com/llm-d/llm-d-batch-gateway)
* [SIG Batch Inference](../../SIGS.md#sig-batch-inference)
