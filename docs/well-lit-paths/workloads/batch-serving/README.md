# Batch Serving

The **Batch Serving** workload umbrella defines recommended, cohesive deployments for processing large-scale, offline, or latency-insensitive tasks on llm-d infrastructure.

Depending on your integration requirements, scale, and operational environment, llm-d offers two distinct paths for queue-based and batch inference:

- **[Batch Gateway](batch-gateway.md)**: An enterprise-grade, fully managed **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`). Best for multi-tenant environments where clients require formal asynchronous job submission, file storage, status tracking, and strict separation of interactive vs. batch compute.
- **[Asynchronous Processing](asynchronous-processing.md)**: A lightweight, low-overhead queue dispatch mechanism (using Redis or GCP Pub/Sub). Best for internal microservice architectures that require low-complexity background task processing or for filling "slack" capacity in your inference pool via dynamic dispatch gating.

## Observability

Both approaches queue work in front of the inference pool, so most issues surface as a backlog. The key question is whether the backlog comes from saturated model servers or from the batch layer not dispatching. See [Observability & Troubleshooting](../../../../guides/batch-serving/README.md#observability--troubleshooting) in the guide for the signals and failure modes of each approach.

## Deploy

See the [Batch Serving Guide](../../../../guides/batch-serving/README.md) for deployment options, comparative analysis, and operational guides.
