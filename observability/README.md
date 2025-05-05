# llm-d Observability

This repository contains monitoring and observability resources for llm-d deployments across different environments. It provides comprehensive tools and configurations to monitor, analyze, and optimize your llm-d deployments in production.

## Getting Started

Choose the appropriate directory based on your deployment environment:

### 🐳 [Kubernetes (k8s)](./k8s)
The Kubernetes monitoring setup provides a complete observability stack for llm-d deployments:
- **Prometheus Integration**: Pre-configured ServiceMonitor resources for automatic metric collection
- **Grafana Dashboards**: Ready-to-use dashboards for real-time monitoring and analysis
- **Metrics Collection**: Comprehensive coverage of system, application, and custom metrics
- **Troubleshooting Tools**: Built-in tools and queries for common operational issues
- [View the full Kubernetes monitoring guide](./k8s/README.md)

### 🔴 [OpenShift](./openshift)
The OpenShift monitoring setup leverages the platform's native capabilities:
- **OpenShift Monitoring Stack**: Integration with the built-in user workload monitoring solution
- **Grafana Operator**: Automated dashboard deployment and management
- [View the full OpenShift monitoring guide](./openshift/README.md)

## Dashboards Overview

The [Dashboards](./dashboards) provide comprehensive monitoring capabilities for llm-d deployments:

### 📊 [Grafana Dashboards](./dashboards)

- `llm-d-dashboard.json` - Main dashboard for monitoring llm-d performance metrics
- `llm-d-dashboard.yaml` - GrafanaDashboard custom resource for monitoring llm-d (vllm metrics)
- `baseline-compare.json` - Dashboard for comparing llm-d with baseline (vllm) performance
- `inference-gateway-dashboard.yaml` - GrafanaDashboard custom resource for monitoring the inference gateway
- Kubernetes deployment configurations for the dashboards

#### Using the Baseline Comparison Dashboard

The baseline comparison dashboard enables detailed real-time performance analysis across namespaces and inference servers.

**Recommended Setup:**
1. Deploy both llm-d and vLLM in separate namespaces
2. Import the dashboard into your Grafana instance
3. Configure namespace variables for each deployment
4. Begin monitoring and analysis

## Additional Resources

- [Metrics Overview](./metrics-overview.md) - Comprehensive guide to available metrics and their usage
- [Example queries for vllm metrics](./query-examples.md) - Ready-to-use Prometheus queries for common monitoring scenarios
- [llm-d Documentation](https://github.com/llm-d/llm-d) - Main project documentation
- [llm-d Deployer](https://github.com/llm-d/llm-d-deployer) - Deployment tools and guides
- [Grafana Documentation](https://grafana.com/docs/) - Learn more about dashboard customization
- [Prometheus Documentation](https://prometheus.io/docs/) - Deep dive into metrics collection
