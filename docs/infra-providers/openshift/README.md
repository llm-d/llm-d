# llm-d-infra Quick Start for OpenShift

## Prerequisites

### Platform Setup

- OpenShift - This guide was tested on OpenShift 4.17. Older versions may work but have not been tested.
- NVIDIA GPU Operator and NFD Operator - The installation instructions can be found [here](https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/steps-overview.html).
- NO Service Mesh or Istio installation as Istio CRDs will conflict with the gateway
- Cluster administrator privileges are required to install the llm-d cluster scoped resources

## llm-d-infra Installation

TBD

## OpenShift and Grafana

If running on OpenShift with user workload monitoring enabled, you can access the metrics through the OpenShift console:

1. Navigate to the OpenShift console
2. In the left navigation bar, click on "Observe"
3. You can access:
   - Metrics: Click on "Metrics" to view and query metrics using the built-in Prometheus UI
   - Targets: Click on "Targets" to see all monitored endpoints and their status

The metrics are automatically integrated into the OpenShift monitoring stack. llm-d-infra does not install Grafana on OpenShift,
but it's recommended that users install Grafana to view metrics and import dashboards.

Follow the [Grafana setup guide](/quickstarts/guide/docs/monitoring/grafana-setup.md).
The guide includes manifests to install the following:

- Grafana instance
- Grafana Prometheus datasource from user workload monitoring stack
- Grafana llm-d dashboard
