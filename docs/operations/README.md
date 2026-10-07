# Operational Excellence

Operational Excellence guidelines focus on overarching Day-2 site reliability engineering, cluster-wide telemetry frameworks, and safe lifecycle rollout strategies for generative AI inference deployments.

While [well-lit path guides](../well-lit-paths/README.md) teach how to configure llm-d's native intelligent routing algorithms and inference optimizations, this top-level section covers how to operate, scale, multiplex, queue, and lifecycle-manage an llm-d fleet in production: capacity and cold starts, multi-tenant traffic and batch, rollouts, observability, and AI gateway integrations. These capabilities are model-agnostic and layer onto any deployment.

## Monitor and Troubleshoot

### [Cluster Observability](observability/README.md)

End-to-end telemetry setup, OpenTelemetry tracing, standard Prometheus metrics, PromQL dashboards, and monitoring architectures.

## Autoscale Inference Pools

### [Autoscaling](../../guides/workload-autoscaling/README.md)

Autoscale the inference pool on proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight requests, token backlog, KV-cache pressure, or predicted latency — using KEDA and EPP metrics.

## Speed Up Model Startup

### [Model Loading & Startup](startup/model-loading-and-startup.md)

Model sources, persistent caches, and startup optimization options for llm-d deployments.

### [Fast Model Actuation](../../guides/fast-model-actuation-base/README.md)

Cut vLLM startup time with resident sleep/wake instances and a pre-warmed launcher that spawns new instances without re-importing modules, so replica scale-up and model swaps avoid the cold-start penalty on a shared GPU pool.

### [FMA with KEDA](../../guides/fast-model-actuation-keda/README.md)

Scale-from-zero autoscaling of Fast Model Actuation on EPP flow-control metrics.

### [Pod Snapshots](../../guides/pod-snapshot/README.md)

Checkpoint and restore single-GPU vLLM model servers to eliminate model download and engine initialization on scale-out, currently implemented with GKE Pod Snapshots and GKE Sandbox (gVisor).

### [ModelExpress P2P Weight Transfer](../../guides/modelexpress-p2p/README.md)

Load one replica from storage and transfer weights to peer replicas over GPU-to-GPU NIXL/RDMA for faster cold scale-outs.

## Manage Inference Pool Lifecycle

### [Graceful Shutdown & Draining](lifecycle/graceful-shutdown.md)

Draining in-flight requests during scale-down, rolling updates, and node drains for general serving: the Kubernetes termination sequence, vLLM `--shutdown-timeout`, request cancellation on client disconnect, and EPP flow-control drain semantics.

### [Readiness Probes](lifecycle/readiness-probes.md)

Kubernetes HTTP probe configurations using vLLM API endpoints to ensure pods are only marked Ready when models are fully loaded.

## Roll Out Updates Safely

### [Rollouts](../../guides/rollouts/README.md)

Production rollout strategies including [Blue-Green updates](../../guides/rollouts/blue-green-update.md) and [live LoRA adapter hot-swapping](../../guides/rollouts/adapter-rollout.md) without dropping active client traffic.

## Operate Disaggregated Serving

### [Disaggregated Serving Operations](disaggregation/README.md): [vLLM](disaggregation/vllm.md) and [SGLang](disaggregation/sglang.md)

Engine-specific operations for disaggregated (prefill/decode) serving: dynamic connections, request cancellation, fault tolerance, and safe rollouts. For the architecture, see [Disaggregated Serving Concepts](../architecture/advanced/disaggregation/README.md).

## Manage Traffic and Tenants

### [Flow Control & Fairness](../../guides/flow-control/README.md)

Intelligent request queuing in the EPP: priority bands, per-tenant fairness, and saturation detection for multi-tenant deployments and traffic spikes. [Production Tuning](../../guides/flow-control/tuning.md) covers sizing `maxConcurrency`.

### [Multi-Model & LoRA Routing](../../guides/multi-model-routing/README.md)

Serve multiple base models and LoRA adapters behind a single endpoint using the Inference Payload Processor (IPP).

## Process Batch and Async Requests

### [Batch & Async Processing](../../guides/batch-serving/README.md)

Process offline and latency-insensitive work in front of an existing deployment: the OpenAI-compatible [Batch Gateway](../../guides/batch-serving/batch-gateway/README.md) (`/v1/batches`, `/v1/files`) and queue-based [Asynchronous Processing](../../guides/batch-serving/asynchronous-processing/README.md) with metric-gated dispatch, including Async Processor sizing and scaling.

## Integrate an AI Gateway

### Serve External APIs: [LiteLLM](integrations/litellm.md) and [Kong AI Gateway](integrations/kong.md)

Deploy LiteLLM Proxy or Kong AI Gateway to route traffic seamlessly between self-hosted llm-d inference stacks and external cloud provider LLM APIs. See [AI Gateway Integrations](integrations/README.md) for the architecture and integration modes.

## Size and Scale llm-d Components

Sizing, high availability, and scaling guidance for running individual llm-d components in production.

### [Router Operations](components/router.md)

Operational best practices, high availability scaling modes, standalone proxy architectures, and container resource sizing for llm-d Router deployments.

### [Async Processor Operations](components/async-processor.md)

Concurrency selection, container resource sizing, and horizontal scaling for the Async Processor dispatch agent.
