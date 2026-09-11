# Operational Excellence

Operational Excellence guidelines focus on overarching Day-2 site reliability engineering, cluster-wide telemetry frameworks, and safe lifecycle rollout strategies for generative AI inference deployments.

While [well-lit path guides](../well-lit-paths/README.md) teach how to configure llm-d's native intelligent routing algorithms and inference optimizations, this top-level section governs enterprise cluster observability, alerting, and zero-downtime model updates.

### [Cluster Observability](observability/README.md)

End-to-end telemetry setup, OpenTelemetry tracing, standard Prometheus metrics, PromQL dashboards, and monitoring architectures.

### [Disaggregated Serving Operations](disaggregation/README.md)

Operational considerations and engine-specific guides (vLLM and SGLang) for dynamic connections, request cancellation, fault tolerance, and safe rollouts.

### [Zero-Downtime Rollouts](rollouts/README.md)

Production rollout strategies including Blue-Green updates and live LoRA adapter hot-swapping without dropping active client traffic.

### [Model-Aware Readiness Probes](readiness-probes.md)

Kubernetes HTTP probe configurations using vLLM API endpoints to ensure pods are only marked Ready when models are fully loaded.

### [Graceful Shutdown & Request Draining](graceful-shutdown.md)

Draining in-flight requests during scale-down, rolling updates, and node drains for general serving: the Kubernetes termination sequence, vLLM `--shutdown-timeout`, request cancellation on client disconnect, and EPP flow-control drain semantics.

### [Serve External APIs](serve-external-apis/README.md)

Deploy LiteLLM Proxy or Kong AI Gateway to route traffic seamlessly between self-hosted llm-d inference stacks and external cloud provider LLM APIs.

### [Router Operations](router.md)

Operational best practices, high availability scaling modes, standalone proxy architectures, and container resource sizing for llm-d Router deployments.

### [Async Processor Operations](async-processor.md)

Throughput modeling, concurrency sizing (backed by a measured sweep), container resource sizing, and horizontal scaling for the Async Processor batch-dispatch agent.
