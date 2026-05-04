# [Experimental] Asynchronous Processing with Async Processor

The [Async Processor](https://github.com/llm-d-incubation/llm-d-async) provides a way to process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive workloads or for filling "slack" capacity in your inference pool.

## Overview

Async Processor integrates with llm-d to:

- **Decouple submission from execution**: Clients submit requests to a queue and retrieve results later.
- **Optimize resource utilization**: Fill idle accelerator time with background tasks.
- **Provide Resilience**: Automatic retries for failed requests without impacting real-time traffic.

### Supported Queue Implementations

1. **[GCP Pub/Sub](./gcp-pubsub/README.md)**: Cloud-native, scalable messaging service.
2. **[Redis Sorted Set](./redis/README.md)**: High-performance, persisted, and prioritized queue implementation.

## Prerequisites

Before installing Async Processor, ensure you have:

1. **Kubernetes cluster**: A running Kubernetes cluster (v1.31+). 
   - For local development, you can use **Kind** or **Minikube**.
   - For production, GKE, AKS, or OpenShift are supported.
2. **Gateway control plane**: Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md) (e.g., Istio) before installation.
3. **llm-d Inference Stack**: Async Processor requires an existing [optimized baseline](../optimized-baseline/README.md) stack to dispatch requests to.

## Installation

Async Processor can be installed via Helm. We recommend following the pattern used in the [optimized baseline](../optimized-baseline/README.md) guide.

#### Step 1: Deploy llm-d Router

Apply the [optimized baseline](../optimized-baseline/README.md) guide and get the llm-d Router's IP address:

```bash
# If using Standalone Mode:
export IP=$(kubectl get service optimized-baseline-epp -n llm-d-optimized-baseline -o jsonpath='{.spec.clusterIP}')

# If using Gateway Mode:
export IP=$(kubectl get gateway llm-d-inference-gateway -n llm-d-optimized-baseline -o jsonpath='{.status.addresses[0].value}')
```

#### Step 2: Configure Values

Choose your queue implementation (GCP Pub/Sub or Redis) and configure the corresponding `values.yaml` file:
- `guides/asynchronous-processing/gcp-pubsub/values.yaml`
- `guides/asynchronous-processing/redis/values.yaml`

#### Step 3: Deploy Async Processor

Deploy the Async Processor using the selected queue implementation's configuration:

```bash
export NAMESPACE=llm-d-async
export MQ_PROVIDER=gcp-pubsub # options are gcp-pubsub or redis
export ASYNC_VERSION=0.6.1

helm install async-processor \
    oci://ghcr.io/llm-d-incubation/charts/async-processor \
    -f guides/asynchronous-processing/${MQ_PROVIDER}/values.yaml \
    --set ap.igwBaseURL=http://${IP}:80 \
    -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}
```

## Testing

Testing instructions vary depending on the chosen queue implementation. Please refer to the specific implementation guide for detailed testing steps:

- [Testing Redis Sorted Set](./redis/README.md#testing)
- [Testing GCP Pub/Sub](./gcp-pubsub/README.md#testing)

## Cleanup

```bash
helm uninstall async-processor -n ${NAMESPACE}
```
