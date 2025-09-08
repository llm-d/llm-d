# Monitoring llm-d with Grafana Operator

This guide was designed for deploying on OpenShift. However, the [grafana resources](./grafana) can be installed on any Kubernetes environment with the [Grafana Operator](https://github.com/grafana/grafana-operator) installed.
If not using OpenShift's built-in Prometheus stack, update the [GrafanaDatasource](./grafana/instance-w-prom-ds/datasource.yaml) to reference the Prometheus URL in your environment.

## Prerequisites

Before you begin, ensure you have:

1. A cluster with administrative access
2. User Workload Monitoring enabled if on OpenShift, or an accessible Prometheus Stack for scraping metrics.
   - See the [OpenShift documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/monitoring/configuring-user-workload-monitoring#enabling-monitoring-for-user-defined-projects_preparing-to-configure-the-monitoring-stack-uwm) to enable this feature
3. ServiceMonitors for scraping metrics from llm-d prefill, decode, and endpoint-picker pods. These are created by the model service Helm chart. Ensure that the `vllm*` and `inference*` metrics are being scraped by querying Prometheus. See the [llm-d metrics overview](./metrics-overview.md) for a list of llm-d metrics.

## Install Grafana Operator

- Install the Grafana Operator from OperatorHub:
  - Go to the OpenShift Console
  - Navigate to Operators -> OperatorHub
  - Search for "Grafana Operator"
  - Click "Install"

- If not on OpenShift, see the [Grafana Operator](https://grafana.github.io/grafana-operator/docs/) documentation to install.

## Install Grafana Resources

1. Create the llm-d-observability namespace:

   ```bash
   kubectl create ns llm-d-observability
   ```

1. Deploy Grafana with Prometheus datasource, llm-d dashboard, and inference-gateway dashboard:

   ```bash
   kubectl apply -n llm-d-observability --kustomize grafana
   ```

   This will:
   - Deploy a Grafana instance
   - Configure the Prometheus datasource to use OpenShift's user workload monitoring
   - Set up basic authentication (username: `admin`, password: `admin`)
   - Create a ConfigMap from the [llm-d dashboard JSON](./dashboards/llm-d-dashboard.json)
   - Deploy the GrafanaDashboard llm-d dashboard that references the ConfigMap
   - Deploy the GrafanaDashboard inference-gateway dashboard that references the upstream
   [k8s-sigs/gateway-api-inference-extension dashboard JSON](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/tools/dashboards/inference_gateway.json)

1. Access Grafana:
   - Go to the OpenShift Console
   - Navigate to Networking -> Routes
   - Find the Grafana route (it will be in the llm-d-observability namespace)
   - Click on the route URL to access Grafana
   - Log in with:
     - Username: `admin`
     - Password: `admin`
     (choose `skip` to keep the default password)

The llm-d and inference-gateway dashboards will be automatically imported and available in your Grafana instance. You can access the dashboard by
clicking on "Dashboards" in the left sidebar and selecting the llm-d dashboard. You can also explore metrics directly using Grafana's Explore page, which is pre-configured to use
OpenShift's user workload monitoring Prometheus instance.

## Additional Resources

- [OpenShift Monitoring Documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/monitoring/index)
- [Grafana Operator Documentation](https://grafana.github.io/grafana-operator/docs/)
