# Production Operations

Production Operations guidelines focus on overarching Day-2 site reliability engineering, cluster-wide telemetry frameworks, and safe lifecycle rollout strategies for generative AI inference deployments.

While [well-lit path guides](../well-lit-paths/README.md) teach how to configure llm-d's native intelligent routing algorithms and inference optimizations, this top-level section governs enterprise cluster observability, alerting, and zero-downtime model updates.

### [Cluster Observability](observability/README.md)
End-to-end telemetry setup, OpenTelemetry tracing, standard Prometheus metrics, PromQL dashboards, and monitoring architectures.

### [Zero-Downtime Rollouts](rollouts/README.md)
Production rollout strategies including Blue-Green updates and live LoRA adapter hot-swapping without dropping active client traffic.
