# Traffic Control & Autoscaling

Traffic Control and Autoscaling guides teach native llm-d runtime request regulation and capacity scaling mechanisms.

While capability building blocks optimize the internal execution paths of individual requests, traffic control and autoscaling mechanisms dynamically regulate incoming traffic pressure and inference pool replica capacity.

- **[Flow Control](flow-control.md)**: Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
- **[Workload Autoscaling](workload-autoscaling.md)**: From simple Kubernetes autoscaling supplemented by EPP load metrics to advanced, SLO-aware capacity optimization for heterogeneous pools via the Workload Variant Autoscaler.
