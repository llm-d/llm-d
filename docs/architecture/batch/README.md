# Batch Inference

llm-d supports batch and offline inference workloads through two components that can be deployed independently or together:

- **[Batch Gateway](batch-gateway.md)** — an OpenAI-compatible Batch API (`/v1/batches`, `/v1/files`) for submitting, tracking, and managing batch inference jobs. Includes an API server and a processor that dispatches individual inference requests to the llm-d Router.

- **[Async Processor](async-processor.md)** — a lightweight dispatch agent that pulls individual inference requests from message queues (such as Redis and Google Pub/Sub) and sends them to the llm-d Router, adjusting the flow based on system metrics.

When deployed together, the Batch Gateway hands off individual requests to the Async Processor instead of dispatching directly, gaining metric-based flow control.

## Observability

Both approaches queue work in front of the inference pool, so most issues surface as a backlog. The key question is whether the backlog comes from saturated model servers or from the batch layer not dispatching. See [Observability & Troubleshooting](../../../guides/batch-serving/README.md#observability--troubleshooting) in the guide for the signals and failure modes of each approach.
