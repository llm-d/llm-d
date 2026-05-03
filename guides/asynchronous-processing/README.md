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

Async Processor can be installed via Helm (using Helmfile) or Kustomize.

### Option 1: Helmfile (Recommended for complex environments)

We provide a `helmfile` for easy deployment.

#### Step 1: Configure llm-d Router URL

The Async Processor needs to know where to send the requests it pulls from the queue. This is configured via the `IGW_BASE_URL` environment variable. 

By default, it is set to `http://infra-optimized-baseline-inference-gateway-istio.llm-d-inference-scheduler.svc.cluster.local:80`.

#### Step 2: Choose your Queue Implementation

Decide whether you want to use GCP Pub/Sub or Redis. Follow the setup instructions in the respective subdirectories:

- [GCP Pub/Sub Setup](./gcp-pubsub/README.md)
- [Redis Setup](./redis/README.md)

#### Step 3: Configure Async Processor Values

Edit the `values.yaml.gotmpl` (and implementation-specific ones) to match your environment.

#### Step 4: Deploy

```bash
export NAMESPACE=llm-d-async
cd guides/asynchronous-processing
helmfile apply -n ${NAMESPACE}
```

### Option 2: Kustomize (Native Kubernetes manifests)

We provide Kustomize manifests for both GCP Pub/Sub and Redis implementations.

#### Step 1: Choose your Implementation

Navigate to the respective manifest directory:

- **GCP Pub/Sub**: `guides/asynchronous-processing/manifests/gcp-pubsub`
- **Redis**: `guides/asynchronous-processing/manifests/redis`

#### Step 2: Configure Values

Edit the `values.yaml` in the chosen directory to match your environment.

#### Step 3: Deploy

```bash
export NAMESPACE=llm-d-async
cd guides/asynchronous-processing/manifests/<implementation>
kustomize build . --enable-helm | kubectl apply -n ${NAMESPACE} -f -
```

## Testing

Testing instructions vary depending on the chosen queue implementation. Please refer to the specific implementation guide for detailed testing steps:

- [Testing Redis Sorted Set](./redis/README.md#testing)
- [Testing GCP Pub/Sub](./gcp-pubsub/README.md#testing)

## Cleanup

### Helmfile
```bash
cd guides/asynchronous-processing
helmfile destroy -n ${NAMESPACE}
```

### Kustomize
```bash
cd guides/asynchronous-processing/manifests/<implementation>
kustomize build . --enable-helm | kubectl delete -n ${NAMESPACE} -f -
```
