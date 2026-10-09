# Operational Excellence

Operational Excellence guidelines focus on overarching Day-2 site reliability engineering, cluster-wide telemetry frameworks, and safe lifecycle rollout strategies for generative AI inference deployments.

While [well-lit path guides](../well-lit-paths/README.md) teach how to configure llm-d's native intelligent routing algorithms and inference optimizations, this top-level section covers how to operate, scale, multiplex, queue, and lifecycle-manage an llm-d fleet in production: capacity and cold starts, multi-tenant traffic and batch, rollouts, observability, and integrations such as AI gateways and the Responses API. These capabilities are model-agnostic and layer onto any deployment.

## Monitor and Troubleshoot

### [Cluster Observability](observability/README.md)

End-to-end telemetry setup, OpenTelemetry tracing, standard Prometheus metrics, PromQL dashboards, and monitoring architectures.

## Autoscale Inference Pools

### [Autoscaling](../../guides/workload-autoscaling/README.md)

Autoscale the inference pool on proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight requests, token backlog, KV-cache pressure, or predicted latency — using KEDA and EPP metrics.

## Speed Up Model Startup

### [Load and Cache Model Weights](startup/model-loading-and-startup.md)

Model sources, node-local and PVC weight caches, self-hosted registries, and compilation-cache reuse for llm-d deployments.

### [Transfer Weights Peer-to-Peer (ModelExpress)](../../guides/modelexpress-p2p/README.md)

Load one replica from storage and transfer weights to peer replicas over GPU-to-GPU NIXL/RDMA for faster cold scale-outs.

### [Restore from Pod Snapshots](../../guides/pod-snapshot/README.md)

Checkpoint and restore single-GPU vLLM model servers to eliminate model download and engine initialization on scale-out, currently implemented with GKE Pod Snapshots and GKE Sandbox (gVisor).

### [Reuse Warm Model Servers (FMA)](../../guides/fast-model-actuation-base/README.md)

Cut vLLM startup time with resident sleep/wake instances and a pre-warmed launcher that spawns new instances without re-importing modules, so replica scale-up and model swaps avoid the cold-start penalty on a shared GPU pool.

### [Scale from Zero with FMA and KEDA](../../guides/fast-model-actuation-keda/README.md)

Scale-from-zero autoscaling of Fast Model Actuation on EPP flow-control metrics.

### [Troubleshoot Model Startup](startup/troubleshooting.md)

Measure cold and warm starts, and fix Hugging Face rate limits and timeouts, startup-probe restarts, and cache storage errors.

## Manage Inference Pool Lifecycle

### [Graceful Shutdown & Draining](lifecycle/graceful-shutdown.md)

Draining in-flight requests during scale-down, rolling updates, and node drains for general serving: the Kubernetes termination sequence, vLLM `--shutdown-timeout`, request cancellation on client disconnect, and EPP flow-control drain semantics.

### [Readiness Probes](lifecycle/readiness-probes.md)

Kubernetes HTTP probe configurations using vLLM API endpoints to ensure pods are only marked Ready when models are fully loaded.

## Roll Out Updates Safely

### [Rollouts](../../guides/rollouts/README.md)

Production rollout strategies including [Blue-Green updates](../../guides/rollouts/blue-green-update.md) and [live LoRA adapter hot-swapping](../../guides/rollouts/adapter-rollout.md) without dropping active client traffic.

## Operate Disaggregated Serving

### [Disaggregated Serving Operations](disaggregation/README.md): [vLLM](disaggregation/vllm.md), [SGLang](disaggregation/sglang.md) and [DisaggregatedSet](disaggregation/disaggregatedset.md)

Engine-specific operations for disaggregated (prefill/decode) serving: dynamic connections, request cancellation, fault tolerance, and safe rollouts. For the architecture, see [Disaggregated Serving Concepts](../architecture/advanced/disaggregation/README.md).

## Manage Traffic and Tenants

### [Queue and Prioritize Requests (Flow Control)](../../guides/flow-control/README.md)

Intelligent request queuing in the EPP: priority bands, per-tenant fairness, and saturation detection for multi-tenant deployments and traffic spikes.

### [Tune Flow Control Concurrency](traffic/flow-control-tuning.md)

Derive `maxConcurrency` for the saturation detector from your hardware, model, and workload with the tuning wizard.

### [Deploy Multiple Inference Pools](../../guides/workload-autoscaling/multi-inference-pool/README.md)

Add InferencePools, each with its own EPP and model server Deployment, to an existing deployment so several models or tenants are served side by side.

### [Route to Multiple Models and LoRA Adapters](../../guides/multi-model-routing/README.md)

Serve multiple base models and LoRA adapters behind a single endpoint using the Inference Payload Processor (IPP).

## Process Batch and Async Requests

Process offline and latency-insensitive work in front of an existing deployment. [Choosing between the two approaches](../../guides/batch-serving/README.md) compares them.

### [Run Batch Jobs (Batch Gateway)](../../guides/batch-serving/batch-gateway/README.md)

Deploy an OpenAI-compatible batch API (`/v1/batches`, `/v1/files`) with pluggable metadata and file storage and a processor that dispatches requests to the llm-d Router.

### [Queue Async Requests (Async Processor)](../../guides/batch-serving/asynchronous-processing/README.md)

Consume requests from a message queue and dispatch them with metric-gated back-off based on live model server saturation, including Async Processor sizing and scaling.

### [Use Redis as the Async Queue](../../guides/batch-serving/asynchronous-processing/redis/README.md)

Configure the Async Processor with a Redis or Valkey sorted set.

### [Use GCP Pub/Sub as the Async Queue](../../guides/batch-serving/asynchronous-processing/gcp-pubsub/README.md)

Configure the Async Processor with Google Cloud Pub/Sub topics and subscriptions.

### [Enforce Tenant Quotas and Priorities](../../guides/batch-serving/asynchronous-processing/multitenant/README.md)

Share async queues across teams with reserved quotas, tier-priority dispatch, and saturation back-off across inference pools.

## Integrations

### Serve External APIs: [LiteLLM](integrations/litellm.md) and [Kong AI Gateway](integrations/kong.md)

Deploy LiteLLM Proxy or Kong AI Gateway to route traffic seamlessly between self-hosted llm-d inference stacks and external cloud provider LLM APIs. See [Integrations](integrations/README.md) for the architecture and integration modes.

### [Serve the Responses API with Agentic API](../../guides/agentic-api/README.md) (Experimental)

Add the OpenAI-compatible Responses API (stateful multi-turn conversations, tool loops, WebSocket streaming) in front of any guide that serves vLLM through the llm-d Router, for agentic clients and coding harnesses.

## Size and Scale llm-d Components

Sizing, high availability, and scaling guidance for running individual llm-d components in production.

### [Router Operations](components/router.md)

Operational best practices, high availability scaling modes, standalone proxy architectures, and container resource sizing for llm-d Router deployments.

### [Async Processor Operations](components/async-processor.md)

Concurrency selection, container resource sizing, and horizontal scaling for the Async Processor dispatch agent.
