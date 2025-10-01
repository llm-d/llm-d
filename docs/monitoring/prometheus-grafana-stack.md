# Quickstart Prometheus Grafana Stack

The [prometheus-grafana setup script](./scripts/install-prometheus-grafana.sh) can be used to deploy a simple observability stack on Kubernetes.

## Step 1: Install Monitoring Stack

Skip if using OpenShift, GKE, or any other Kubernetes environment where Prometheus already exists and is accessible:

```bash
# Central monitoring (default - Prometheus monitors all namespaces)
./scripts/install-prometheus-grafana.sh

# Central monitoring in custom namespace
./scripts/install-prometheus-grafana.sh -n monitoring

# Individual user monitoring (isolated - Prometheus only monitors selected namespaces based on namespace labels)
./scripts/install-prometheus-grafana.sh --individual -n my-monitoring-namespace
```

## Step 2: Enable Monitoring for Your Deployments

Choose the approach that matches your monitoring setup:

### Option A: Central Monitoring (Default)

**No additional configuration required!** Central monitoring automatically discovers all ServiceMonitors and PodMonitors across all namespaces.

### Option B: Individual User Monitoring

For Prometheus to watch llm-d PodMonitors, label the namespace where llm-d is running.

```bash
# Replace 'my-monitoring-namespace' and 'my-llm-d-namespace' with your actual namespaces
kubectl label namespace my-llm-d-namespace monitoring-ns=my-monitoring-namespace
```

## Step 3: Enable Metrics in Your Deployments

In any llm-d helmfile example, update the modelservice values to enable monitoring:

```yaml
# In ms-*/values.yaml files
decode:
  monitoring:
    podmonitor:
      enabled: true

prefill:
  monitoring:
    podmonitor:
      enabled: true
```

## Step 4: Access Prometheus & Grafana UIs

```bash
kubectl port-forward -n <your-monitoring-namespace> svc/prometheus-kube-prometheus-prometheus 9090:9090
# Visit http://localhost:9090

kubectl port-forward -n <your-monitoring-namespace> svc/prometheus-grafana 3000:80
# Visit http://localhost:3000

# Grafana login: admin/admin
```

## Cleanup

To remove the Prometheus and Grafana stack:

```bash
./scripts/install-prometheus-grafana.sh -u -n <your-monitoring-namespace>
```
