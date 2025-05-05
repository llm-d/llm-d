# Monitoring llm-d in Kubernetes

This guide will help you set up monitoring and visualization for `llm-d` components running in Kubernetes.
It will describe how to deploy Prometheus and Grafana, and configure them to collect and visualize `llm-d` metrics.

## Prerequisites

If you have used the [quickstart](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README.md) or the
[miniKube quickstart](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README-minikube.md)
to deploy `llm-d`, the quickstart scripts will deploy the Prometheus operator and Prometheus with Grafana, along with the llm-d
modelservice `ServiceMonitor`. The resources will be running in namespace `llm-d-monitoring`.
Skip to the [Metrics Collection](#step-3-metrics-collection) section below if you deployed llm-d with the quickstart installer.

If you have not deployed using the quickstart scripts, in order to install the monitoring stack,
ensure you have:

1. A Kubernetes cluster

2. `kubectl` configured to communicate with your cluster

3. `helm` installed

## Step 1: Deploy Prometheus and Grafana Stack

We'll use the kube-prometheus-stack Helm chart which includes Prometheus, Grafana, and the Prometheus Operator:

1. Add the prometheus-community Helm repository:
   ```bash
   helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
   helm repo update
   ```

2. Create the llm-d-observability namespace:

   ```bash
   kubectl create namespace llm-d-observability
   ```

3. Install the kube-prometheus-stack with minimal configuration:

   ```bash
   # Create a values file for minimal configuration
   cat <<EOF > /tmp/prometheus-values.yaml
   grafana:
     adminPassword: admin
     service:
       type: ClusterIP
   prometheus:
     service:
       type: ClusterIP
     prometheusSpec:
       serviceMonitorSelectorNilUsesHelmValues: false
       serviceMonitorSelector: {}
       serviceMonitorNamespaceSelector: {}
   EOF

   helm install prometheus prometheus-community/kube-prometheus-stack \
     --namespace llm-d-observability \
     -f /tmp/prometheus-values.yaml

   rm -f /tmp/prometheus-values.yaml
   ```

   This will:
   - Deploy Prometheus Operator
   - Deploy Prometheus instance with ClusterIP service
   - Deploy Grafana with ClusterIP service
   - Configure basic RBAC
   - Enable ServiceMonitor discovery across all namespaces

4. Wait for the pods to be ready:

   ```bash
   kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n llm-d-observability --timeout=300s
   kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=grafana -n llm-d-observability --timeout=300s
   ```

## Step 2: Ensure ServiceMonitors

The llm-d components expose metrics that need to be scraped by Prometheus.
The llm-d-deployer charts include a `ServiceMonitor` template that will be configured and applied
when you deploy llm-d unless you disable ServiceMonitor creation in the values file.
ServiceMonitors are provided by Prometheus operator, they trigger scrape targets for pods in the cluster associated with matching services.

If you need to add other ServiceMonitors outside of the ones included with llm-d-deployer,
here's an example ServiceMonitor configuration:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: example-service
  labels:
    app.kubernetes.io/name: example-service
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: example-service
  endpoints:
  - port: metrics
    interval: 15s
    path: /metrics
  namespaceSelector:
    matchNames:
      - default  # Change this to your service's namespace
```

## Step 3: Metrics Collection

After deploying llm-d or applying a ServiceMonitor, you can verify and explore metrics in two ways:

1. Using Grafana's Explore feature (Recommended):
   - With Grafana port-forwarded (as described above), click on "Explore" in the left sidebar
   - Select "Prometheus" as the data source
   - Use the query builder or write PromQL queries directly
   - This provides a more user-friendly interface with better visualization options

2. Using Prometheus UI (for target monitoring):
   - With Prometheus port-forwarded (as described above), go to Status -> Targets
   - Verify the metrics endpoints are being scraped. You should see:
     - Your llm-d service endpoints listed as targets
     - The "State" column should show "UP" for healthy endpoints
     - Check the "Last Scrape" column to confirm recent data collection

## Step 4: Deploy the llm-d Dashboard

The llm-d dashboard provides comprehensive monitoring of your model server's performance. To deploy it:

1. With Grafana port-forwarded (as described above), click on the "+" icon in the left sidebar and select "Import"

2. Click "Upload JSON file" and select the `llm-d-dashboard.json` file from the `../dashboards` directory

3. In the import form:
   - Set the Prometheus data source (it should be pre-configured as "Prometheus")
   - Optionally, you can set a custom dashboard name and folder
   - Click "Import"

The dashboard will now be available in your Grafana instance, showing metrics like:
- End-to-end request latency
- Tokens processed per second
- Request throughput
- Model performance metrics

You can access the dashboard by clicking on "Dashboards" in the left sidebar and selecting the llm-d dashboard.

## Troubleshooting

If you don't see metrics:

1. Verify that Prometheus Operator is running:

   ```bash
   kubectl get pods -n llm-d-observability # quickstart deploys in llm-d-monitoring
   ```

2. Check that your llm-d pods/services are running
3. Ensure the metrics endpoint is accessible
4. Verify the ServiceMonitor selectors match your workload
5. Check Prometheus targets page for any scrape errors

## Additional Resources

- [llm-d-deployer miniKube quickstart](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README-minikube.md)
- [llm-d-deployer quickstart](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README.md)
- [kube-prometheus-stack Helm Chart](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [Grafana Documentation](https://grafana.com/docs/)
- [Kubernetes Monitoring Best Practices](https://kubernetes.io/docs/concepts/cluster-administration/monitoring/)
- [Minikube Ingress Documentation](https://minikube.sigs.k8s.io/docs/handbook/addons/ingress-addon/) 
