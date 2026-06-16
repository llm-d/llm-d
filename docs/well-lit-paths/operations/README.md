# Operational Excellence

Operational Excellence guides focus on platform stability traits, traffic governance, capacity optimization, and non-Kubernetes environmental adaptations.

While capability building blocks optimize the internal execution paths of requests, operational excellence guides govern overarching cluster resources, traffic limits, and platform health.

- **[Flow Control](flow-control.md)**: Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
- **[Workload Autoscaling](workload-autoscaling.md)**: From simple Kubernetes autoscaling supplemented by EPP load metrics to advanced, SLO-aware capacity optimization for heterogeneous pools via the Workload Variant Autoscaler.
- **[No-Kubernetes Deployment](no-kubernetes-deployment.md)**: Running the llm-d routing stack on bare metal, HPC schedulers, or Ray — workers are discovered from a YAML file on disk via the `file-discovery` plugin instead of an `InferencePool`.
