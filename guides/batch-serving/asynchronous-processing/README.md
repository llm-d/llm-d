# Asynchronous Processing with Async Processor

The [Async Processor](https://github.com/llm-d/llm-d-async) provides a way to process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive workloads or for filling "slack" capacity in your inference pool.

Two guides are available:

- **This guide** — the standard **single-model** setup (choose a queue backend below and deploy).
- **[Multi-tenant guide](./multitenant/README.md)** — the **advanced** setup: **team × tier × model** with per-team reserved/overflow quota (classifying `redis-quota`), tier-priority dispatch, and per-model saturation back-off across two `InferencePool`s. Runs on either queue backend.

> [!NOTE]
> For production sizing, scaling, and container-resource guidance, see [Async Processor Operations](../../../docs/operations/components/async-processor.md).

## Overview

This guide deploys the Async Processor (Helm chart `llm-d-async`) in front of an existing [optimized baseline](../../optimized-baseline/README.md) stack. It consumes requests from one message queue — GCP Pub/Sub by default, or a Redis Sorted Set — and dispatches them to the llm-d Router, either directly to the router Service (Standalone mode) or through the Gateway (Gateway mode).

Clients enqueue work and read results from the result topic or list later instead of holding an HTTP connection open, and retries happen without touching real-time traffic. Dispatch gates, set in the Helm values, decide when queued work is released — for example only while the model servers have spare capacity.

For how the processor works — dispatch gates, worker pools, merge policies, retries and deadlines, and queue semantics — see the [Async Processor Architecture](../../../docs/architecture/advanced/batch/async-processor.md).

### When to Use This Path

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
- [Async Processor Architecture](../../../docs/architecture/advanced/batch/async-processor.md) — internal mechanics, gates, and queue integrations.
