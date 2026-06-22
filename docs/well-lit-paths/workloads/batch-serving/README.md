# Batch Serving

The **Batch Serving** workload umbrella defines recommended, cohesive deployments for processing large-scale, offline, or latency-insensitive tasks on llm-d infrastructure.

Depending on your integration requirements, scale, and operational environment, llm-d offers two distinct paths for queue-based and batch inference:

- **[Batch Gateway](batch-gateway.md)**: An enterprise-grade, fully managed **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`). Best for multi-tenant environments where clients require formal asynchronous job submission, file storage, status tracking, and strict separation of interactive vs. batch compute.
- **[Asynchronous Processing](asynchronous-processing.md)**: A lightweight, low-overhead queue dispatch mechanism (using Redis or GCP Pub/Sub). Best for internal microservice architectures that require low-complexity background task processing or for filling "slack" capacity in your inference pool via dynamic dispatch gating.
