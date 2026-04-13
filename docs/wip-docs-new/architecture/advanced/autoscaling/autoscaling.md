# Autoscaling

With autoscaling, Model Servers scale up and down automatically based on demand, ensuring efficient resource utilization while meeting latency SLOs. llm-d supports two complementary autoscaling approaches:

- **HPA/KEDA with EPP Queue Metrics** - A straightforward approach that uses Kubernetes Horizontal Pod Autoscaler (HPA) or KEDA to scale Model Server replicas based on queue-depth metrics exported by the EPP. This is well-suited for homogeneous deployments where a single topology serves a model.

- **Weighted Variant Autoscaler (WVA)** - A global optimizer that dynamically calculates the optimal mix of accelerator topologies and Model Server configurations to serve the current traffic load at the least cost. The WVA considers heterogeneous hardware, disaggregated serving roles (prefill, decode, or both), and changing traffic patterns to determine the desired deployment state.

See [Autoscaling](advanced/autoscaling/autoscaling.md) for complete details on the autoscaling design.
