# Asynchronous Processing with Async Processor

The [Async Processor](https://github.com/llm-d/llm-d-async) provides a way to process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive workloads or for filling "slack" capacity in your inference pool.

Two guides are available:

- **This guide** — the standard **single-model** setup (choose a queue backend below and deploy).
- **[Multi-tenant guide](./multitenant/README.md)** — the **advanced** setup: **team × tier × model** with per-team reserved/overflow quota (classifying `redis-quota`), tier-priority dispatch, and per-model saturation back-off across two `InferencePool`s. Runs on either queue backend.

> [!NOTE]
> For production sizing, scaling, and container-resource guidance, see [Async Processor Operations](../../../docs/operations/components/async-processor.md).

## Overview

Async Processor integrates with llm-d to:

- **Decouple submission from execution**: Clients submit requests to a queue and retrieve results later.
- **Optimize resource utilization**: Fill idle accelerator time with background tasks.
- **Provide Resilience**: Automatic retries for failed requests without impacting real-time traffic.

It decouples request submission from execution, allowing clients to submit large volumes of work without maintaining a long-lived HTTP connection.

### Architecture

The **Async Processor** is a lightweight dispatch agent that pulls requests from a message queue and forwards them to the llm-d Router.

To prevent background tasks from impacting real-time traffic, the Async Processor uses **Dispatch Gates**. These gates regulate the flow of requests based on system metrics:

- **Prometheus Gating**: Queries model server saturation (e.g., KV cache pressure, queue depth) and only dispatches when the system has available "slack" capacity.
- **Budget Gating**: Uses a pre-calculated budget to control throughput.
- **Priority & Deadlines**: Requests can be prioritized, and the processor enforces deadlines to ensure stale work is abandoned.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img src="../../../docs/assets/async-processor.svg" alt="Async Processor Architecture">
  </picture>
</p>

Transient failures (like rate limits or network issues) are automatically re-queued with exponential backoff, and configurable worker pools tune the degree of parallelism for background processing (see [Async Processor Operations](../../../docs/operations/components/async-processor.md)). See the [Async Processor Architecture](../../../docs/architecture/batch/async-processor.md) for more details on the internal mechanics.

### Use Cases

- **Batch Inference**: Processing large datasets where completion time is measured in minutes or hours rather than milliseconds.
- **Slack Capacity Filling**: Using idle GPU cycles between real-time request spikes to perform background tasks like document summarization or embedding generation.
- **Offline Evaluation**: Running model evaluation pipelines without competing for production resources.

### Supported Queue Implementations

1. **[GCP Pub/Sub](./gcp-pubsub/README.md)**: Cloud-native, scalable messaging service.
2. **[Redis Sorted Set](./redis/README.md)**: High-performance, persisted, and prioritized queue implementation.

## Prerequisites

Before installing Async Processor, ensure you have:

1. **Kubernetes cluster**: A running Kubernetes cluster (v1.31+).
   - For local development, you can use **Kind** or **Minikube**.
   - For production, GKE, AKS, or OpenShift are supported.
2. **Gateway control plane** (Gateway mode only): if you run the optimized baseline behind a Gateway, configure and deploy your [Gateway control plane](../../../docs/infrastructure/gateway/README.md) (e.g., Istio) before installation. In Standalone mode the Async Processor dispatches to the llm-d Router directly and no Gateway is needed.
3. **llm-d Inference Stack**: Async Processor requires an existing [optimized baseline](../../optimized-baseline/README.md) stack to dispatch requests to.

## Installation

Async Processor can be installed via Helm. We recommend following the pattern used in the [optimized baseline](../../optimized-baseline/README.md) guide.

#### Step 1: Deploy llm-d Router

Apply the [optimized baseline](../../optimized-baseline/README.md) guide and get the llm-d Router's IP address:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
# If using Standalone Mode:
export IP=$(kubectl get service optimized-baseline-epp -n llm-d-optimized-baseline -o jsonpath='{.spec.clusterIP}')

# If using Gateway Mode:
export IP=$(kubectl get gateway llm-d-inference-gateway -n llm-d-optimized-baseline -o jsonpath='{.status.addresses[0].value}')
```

#### Step 2: Configure Values

Choose your queue implementation (GCP Pub/Sub or Redis) and configure the corresponding `values.yaml` file:

- `guides/batch-serving/asynchronous-processing/gcp-pubsub/values.yaml`
- `guides/batch-serving/asynchronous-processing/redis/values.yaml`

#### Step 3: Deploy Async Processor

Deploy the Async Processor using the selected queue implementation's configuration:

```bash
export NAMESPACE=llm-d-async
export MQ_PROVIDER=gcp-pubsub # options are gcp-pubsub or redis
export ASYNC_VERSION=v0.10.0   # llm-d-async release

[ "$MQ_PROVIDER" = "redis" ] && TARGET_KEY="ap.transportConfig.queues[0].igw_base_url" || TARGET_KEY="ap.transportConfig.topics[0].igw_base_url"

helm install llm-d-async \
    oci://ghcr.io/llm-d/charts/llm-d-async \
    -f ${REPO_ROOT}/guides/batch-serving/asynchronous-processing/${MQ_PROVIDER}/values.yaml \
    --set ${TARGET_KEY}=http://${IP}:80 \
    -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}
```

## Testing

Testing instructions vary depending on the chosen queue implementation. Please refer to the specific implementation guide for detailed testing steps:

- [Testing Redis Sorted Set](./redis/README.md#testing)
- [Testing GCP Pub/Sub](./gcp-pubsub/README.md#testing)

## Cleanup

```bash
helm uninstall llm-d-async -n ${NAMESPACE}
```

## Related

- [Async Processor Operations](../../../docs/operations/components/async-processor.md) — concurrency, container sizing, and horizontal scaling.
- [Async Processor Architecture](../../../docs/architecture/batch/async-processor.md) — internal mechanics, gates, and queue integrations.
