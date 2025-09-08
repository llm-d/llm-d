# llm-d Observability & Monitoring Guide

This guide explains how to set up monitoring and observability for llm-d deployments.

## Overview

We provide a Prometheus and Grafana installation script with two deployment patterns:

1. **Central Monitoring (Default)**: Single Prometheus instance monitors all namespaces automatically
2. **Individual User Monitoring**: Each user has their own isolated Prometheus/Grafana stack

This generally requires cluster administration rights if you are deploying your own stack.

## Platform-Specific Setup

### Kubernetes Clusters

If you're running on **standard Kubernetes** and you don't already have a Prometheus or observability stack deployed,
use the `install-prometheus-grafana.sh` script to deploy a Prometheus and Grafana stack for metrics collection.

### OpenShift Clusters

If you're running on **OpenShift**, you **do not need** to deploy Prometheus using this script. Instead, use OpenShift's built-in user workload monitoring:

1. Enable user workload monitoring (if not already enabled)
2. Deploy your llm-d workloads with PodMonitor enabled
3. Metrics will automatically be collected by OpenShift's monitoring stack

The `install-prometheus-grafana.sh` script will detect OpenShift and guide you through enabling user workload monitoring if needed.

## Key Requirements

- **Individual mode requires explicit namespace**: Use `-n <namespace>` or set `MONITORING_NAMESPACE` environment variable
- **Central mode supports custom namespaces**: Use `-n <namespace>` to install in any namespace (defaults to `llm-d-monitoring`)
- **Unique release names**: Each installation uses `prometheus-<namespace>` as the release name to avoid conflicts
- **Label-based targeting**: Individual mode uses `monitoring-ns=<namespace>` labels to target specific namespaces

## Quick Start

### Step 1: Install Monitoring Stack

**For Kubernetes clusters only** (skip if using OpenShift):

```bash
# Central monitoring (default - monitors all namespaces)
./install-prometheus-grafana.sh

# Central monitoring in custom namespace
./install-prometheus-grafana.sh -n monitoring

# Individual user monitoring (isolated) - requires explicit namespace
./install-prometheus-grafana.sh --individual -n my-monitoring-namespace
```

**For OpenShift clusters**: No Prometheus installation needed - use the built-in user workload monitoring.

### Step 2: Enable Monitoring for Your Deployments

Choose the approach that matches your monitoring setup:

#### Option A: Central Monitoring (Default)

**No additional configuration required!** Central monitoring automatically discovers all ServiceMonitors and PodMonitors across all namespaces.

#### Option B: Individual User Monitoring

```bash
# Label your namespace for individual monitoring
# Replace 'my-monitoring-namespace' with your actual monitoring namespace
kubectl label namespace <your-namespace> monitoring-ns=my-monitoring-namespace
```

### Step 3: Enable Metrics in Your Deployments

Update your modelservice values to enable monitoring:

```yaml
# In your ms-*/values.yaml files
decode:
  monitoring:
    podmonitor:
      enabled: true

prefill:
  monitoring:
    podmonitor:
      enabled: true
```

### Step 4: Access Dashboards

```bash
# For central monitoring (default namespace)
kubectl port-forward -n llm-d-monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
kubectl port-forward -n llm-d-monitoring svc/prometheus-grafana 3000:80

# For central monitoring (custom namespace)
kubectl port-forward -n <your-namespace> svc/prometheus-kube-prometheus-prometheus 9090:9090
kubectl port-forward -n <your-namespace> svc/prometheus-grafana 3000:80

# For individual monitoring
kubectl port-forward -n <your-monitoring-namespace> svc/prometheus-kube-prometheus-prometheus 9090:9090
kubectl port-forward -n <your-monitoring-namespace> svc/prometheus-grafana 3000:80

# Grafana login: admin/admin
```

## Integration with Helmfile Examples

### Central Monitoring (Recommended)

Since central monitoring watches all namespaces automatically, no additional configuration is needed in your helmfiles. Simply enable monitoring in your values files:

```yaml
# In your ms-*/values.yaml
decode:
  monitoring:
    podmonitor:
      enabled: true

prefill:
  monitoring:
    podmonitor:
      enabled: true
```

### Individual Monitoring Integration

Add this hook to your `helmfile.yaml` to automatically label namespaces:

```yaml
# Add to any helmfile.yaml for individual monitoring
# Replace 'my-monitoring-namespace' with your actual monitoring namespace
hooks:
  - name: enable-monitoring
    events: ["postsync"]
    command: kubectl
    args:
      - label
      - namespace
      - <your-namespace>  # Replace with actual namespace
      - monitoring-ns=my-monitoring-namespace
      - --overwrite
```

### Manual Namespace Labeling (Individual Mode Only)

For manual control with individual monitoring:

```bash
# After running helmfile sync
# Replace 'my-monitoring-namespace' with your actual monitoring namespace
kubectl label namespace llm-d-sim monitoring-ns=my-monitoring-namespace                     # For sim example
kubectl label namespace llm-d-pd monitoring-ns=my-monitoring-namespace                      # For pd-disaggregation example
```

## Security & Multi-Tenant Considerations

### Central Monitoring

- ⚠️ **Single-tenant use only**: All users can see all metrics
- **Permissions**: Cluster-admin required for installation
- **Isolation**: No tenant isolation - suitable for trusted environments
- **Simplicity**: Zero configuration for metric collection

### Individual Monitoring

- ✅ **Multi-tenant safe**: Users only see their own metrics
- **Permissions**: Namespace creation + ServiceAccount management
- **Isolation**: Complete isolation between users
- **Configuration**: Requires namespace labeling

### Debugging Commands

```bash
# Check Prometheus targets (central mode - default namespace)
kubectl port-forward -n llm-d-monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
# Visit http://localhost:9090/targets

# Check Prometheus targets (individual mode)
kubectl port-forward -n <your-monitoring-namespace> svc/prometheus-kube-prometheus-prometheus 9090:9090
# Visit http://localhost:9090/targets

# Check ServiceMonitor/PodMonitor resources
kubectl get servicemonitor,podmonitor -A

# Check namespace labels (individual mode only)
kubectl get namespaces --show-labels | grep monitoring-ns
```

## Cleanup

```bash
# Remove central monitoring stack (default namespace)
./install-prometheus-grafana.sh -u

# Remove central monitoring stack (custom namespace)
./install-prometheus-grafana.sh -u -n monitoring

# Remove individual monitoring stack
./install-prometheus-grafana.sh -u -n <your-monitoring-namespace>

# Remove namespace labels (individual mode only)
kubectl label namespace <your-ns> monitoring-ns-
```
