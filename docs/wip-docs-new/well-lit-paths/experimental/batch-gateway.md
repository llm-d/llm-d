# Batch Gateway

Process large-scale batch inference jobs via an OpenAI-compatible API, enabling batch and interactive workloads to coexist efficiently on shared infrastructure.

## When to Pick This Path

- You have **offline inference workloads** (such as evaluations, embeddings, dataset processing) that don't need real-time responses.
- You want to **utilize idle accelerator capacity** for batch work while protecting interactive traffic from interference.
- Your clients expect an **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`) for job submission, tracking and management.
- You need **multi-tenant isolation** — each tenant's jobs, files, and results are separated.

## Prerequisites

- A working llm-d Router, inference pool, and at least one model server. If you don't have this, start with [getting-started/quickstart.md](../../getting-started/quickstart.md).
- PostgreSQL (12+) and Redis (6+) or Valkey (8+) accessible from the cluster.
- S3-compatible storage or a shared PVC with `ReadWriteMany` (RWX) access mode for batch input/output files.
- Helm 3.0+.

## Related

- [Batch Gateway Deployment Guide](https://github.com/llm-d/llm-d/tree/main/guides/batch-gateway) — full deployment instructions, configuration options, and troubleshooting.
- [Batch Gateway Architecture](../../architecture/advanced/batch/batch-gateway.md) — components, data flow, and processing pipeline.
- [Batch Gateway Repository](https://github.com/llm-d-incubation/batch-gateway) — source code, Helm chart, platform-specific deployment guides, and demo scripts.
- [Asynchronous Processing](https://github.com/llm-d/llm-d/tree/main/guides/asynchronous-processing) — complementary queue-based async inference for individual requests.
