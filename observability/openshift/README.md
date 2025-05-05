# Monitoring llm-d in OpenShift

This guide will help you set up monitoring and visualization for `llm-d` components running in OpenShift.
It will enable collection of metrics and will describe how to view them in both OpenShift's built-in monitoring and Grafana dashboards.

## Prerequisites

Before you begin, ensure you have:

1. An OpenShift cluster with administrative access
2. User Workload Monitoring enabled in your cluster
   - Follow the [official OpenShift documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/monitoring/configuring-user-workload-monitoring#enabling-monitoring-for-user-defined-projects_preparing-to-configure-the-monitoring-stack-uwm) to enable this feature

## Step 1: Metrics Collection

The llm-d components expose metrics that need to be scraped by Prometheus.
The llm-d-deployer charts include `ServiceMonitor` templates that will be configured when you deploy llm-d by default.
ServiceMonitors are provided by Prometheus operator, and are recognized by OpenShift's built-in Prometheus stack. ServiceMonitors
trigger scrape targets for pods in the cluster associated with matching services.

Upon a successful `llm-d` helm install, you can view the metrics in OpenShift:

1. Go to the OpenShift Console

2. Navigate to `Observe -> Metrics`

3. You should see llm-d metrics being collected

## Step 2: Set Up Grafana (Optional)

For more advanced visualization, you can set up Grafana:

1. Install Grafana Operator from OperatorHub:
   - Go to the OpenShift Console
   - Navigate to Operators -> OperatorHub
   - Search for "Grafana Operator"
   - Click "Install"

2. Create the llm-d-observability namespace:

   ```bash
   oc create ns llm-d-observability
   ```

3. Deploy Grafana with Prometheus datasource, llm-d dashboard, and inference-gateway dashboard:

   ```bash
   oc apply -n llm-d-observability --kustomize ./openshift/grafana
   ```

   This will:
   - Deploy a Grafana instance
   - Configure the Prometheus datasource to use OpenShift's user workload monitoring
   - Set up basic authentication (username: `admin`, password: `admin`)
   - Create a ConfigMap from the [llm-d dashboard JSON](../dashboards/llm-d-dashboard.json)
   - Deploy the GrafanaDashboard llm-d dashboard that references the ConfigMap
   - Deploy the GrafanaDashboard inference-gateway dashboard that references the upstream
   [k8s-sigs/gateway-api-inference-extension dashboard JSON](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/tools/dashboards/inference_gateway.json)

5. Access Grafana:
   - Go to the OpenShift Console
   - Navigate to Networking -> Routes
   - Find the Grafana route (it will be in the llm-d-observability namespace)
   - Click on the route URL to access Grafana
   - Log in with:
     - Username: `admin`
     - Password: `admin`
     (choose `skip` to keep the default password)

6. The llm-d and inference-gateway dashboards will be automatically imported and available in your Grafana instance, showing metrics like:
   - End-to-end request latency
   - Tokens processed per second
   - Request throughput
   - Model performance metrics
   - Inference details

   You can access the dashboard by clicking on "Dashboards" in the left sidebar and selecting the llm-d dashboard.

   You can also explore metrics directly using Grafana's Explore page, which is pre-configured to use OpenShift's user workload monitoring Prometheus instance.

## Troubleshooting

If you don't see metrics:
1. Verify that User Workload Monitoring is enabled
2. Check that your llm-d pods/services are running
3. Ensure the metrics endpoint is accessible
4. Verify the ServiceMonitor selectors match your workload
5. Check Prometheus targets page for any scrape errors

## Additional Resources

- [llm-d-deployer helm charts](https://github.com/llm-d/llm-d-deployer/tree/main/charts/llm-d)
- [OpenShift Monitoring Documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/monitoring/index)
- [Grafana Operator Documentation](https://github.com/grafana-operator/grafana-operator)
