# Autoscaling with Workload Variant Autoscaler (WVA)

## **MATURITY** : Experimental (not regularly tested by maintainers)

The [Workload Variant Autoscaler](https://github.com/llm-d-incubation/workload-variant-autoscaler) (WVA) provides dynamic autoscaling capabilities for llm-d inference deployments, automatically adjusting replica counts based on inference server saturation.

## Overview

WVA integrates with llm-d to:

- Dynamically scale inference replicas based on workload saturation
- Optimize resource utilization by adjusting to traffic patterns
- Reduce tail latency through saturation-based scaling decisions

## Prerequisites

Before installing WVA, ensure you have:

1. Installed the llm-d inference stack from one of the well-lit path guides.

    > **Note**: WVA requires HTTPS connections to Prometheus for metric collection. When installing the [monitoring stack](../../docs/monitoring/README.md), ensure to enable HTTPS/TLS support.

    > **Note**: If selecting namespace-scoped mode below, make sure to install the inference stack in the same namespace as WVA (by default `llm-d-autoscaler`).

    > **Notes**: currently WVA does not support the Wide Expert Parallelism (EP/DP) with LeaderWorkerSet well-lit path. Support for this will be added in a future release.

1. An external metrics provider installed and configured in your cluster (e.g., Prometheus together with Prometheus Adapter or KEDA). WVA relies on external metrics to make scaling decisions. See [Install Prometheus Adapter (Required Dependency)](#install-prometheus-adapter-required-dependency) for installation instructions.

1. Choose an [llm-version](../prereq/client-setup/README.md#llm-d-version).

## Set your Namespace

  ```bash
  export NAMESPACE=llm-d-autoscaler
  kubectl create namespace ${NAMESPACE}
  ```

  **For OpenShift only**, ensure the namespace has the monitoring label:

  ```bash
  kubectl label namespace "${NAMESPACE}" openshift.io/user-monitoring=true --overwrite
  ```

## Platform-Specific Configuration

### OpenShift

For OpenShift deployments, use the OpenShift-specific Helm values file which configures WVA to connect to the cluster's Thanos Querier for metrics:

```bash
export HELM_VALUES=workload-autoscaling/values-openshift.yaml
export CA_CERT=$(kubectl get secret thanos-querier-tls -n openshift-monitoring -o jsonpath='{.data.tls\.crt}' | base64 -d)
```

### GKE

GMP doesn't expose HTTP API. Deploy in-cluster Prometheus:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n llm-d-monitoring --create-namespace
```

Then use the GKE-specific Helm values file which configures WVA to connect to the in-cluster Prometheus:

```bash
export HELM_VALUES=workload-autoscaling/values-gke.yaml
export CA_CERT=
```

## Installation

Install the WVA controller using helm:

  ```bash
  export VERSION=0.5.1
  helm install llm-d-wva oci://ghcr.io/llm-d/workload-variant-autoscaler \
    --namespace ${NAMESPACE} \
    --version ${VERSION} \
    --set wva.prometheus.caCert="$(echo "${CA_CERT}")" \
    --values ${HELM_VALUES:=workload-autoscaling/values.yaml}
  ```

  > **Note**: By default, WVA is configured to watch all namespaces for VariantAutoscaling resources. You can scope this to the namespace where WVA is installed by setting the `namespaceScoped` flag to `true`: `--set wva.namespaceScoped=true`

## Verify Installation

Check that the WVA controller is running:

```bash
kubectl get deployment -n ${NAMESPACE}
NAME                                                       READY   UP-TO-DATE   AVAILABLE   AGE
llm-d-wva-workload-variant-autoscaler-controller-manager   2/2     2            2           10m
```

## Enabling Autoscaling for an Inference Deployment

This section enables autoscaling for an existing [Intelligent Inference Scheduling](../inference-scheduling/README.md) deployment. It creates a `VariantAutoscaling` CR, and an HPA that reads the `wva_desired_replicas` metric.

### Apply the Kustomize Overlay

```bash
kubectl apply -k inference-scheduling-autoscaling -n ${NAMESPACE}
```

> **Note:** cluster-scoped mode: `${NAMESPACE}` must match the namespace where the inference-scheduling stack is running (default: `llm-d-inference-scheduler`).

> **Note:** namespace-scoped mode: `${NAMESPACE}` must match the namespace where the WVA is running (default: `llm-d-autoscaler`).

> **Note:** If you set the `RELEASE_NAME_POSTFIX` environment variable when installing the inference-scheduling stack, you need to set the same postfix in the `kustomization.yaml` of this overlay to ensure the correct resources are targeted. For example, if you set `RELEASE_NAME_POSTFIX=my-custom` during installation, you should uncomment the line `nameSuffix: -my-custom` in the `kustomization.yaml` of this overlay.

### Verify

After a few minutes, you should see the new `VariantAutoscaling` resource:

```bash
kubectl get variantautoscaling ms-inference-scheduling-llm-d-modelservice-decode -n ${NAMESPACE}
```

Expected output:

```
NAME                                                TARGET                                              MODEL            OPTIMIZED   METRICSREADY   AGE
ms-inference-scheduling-llm-d-modelservice-decode   ms-inference-scheduling-llm-d-modelservice-decode   Qwen/Qwen3-32B   1           True           37m
```

You should also see the HPA with the `wva_desired_replicas` metric:

```bash
kubectl get hpa -n ${NAMESPACE}
NAME                                                REFERENCE                                              TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
ms-inference-scheduling-llm-d-modelservice-decode   Deployment/ms-inference-scheduling-llm-d-modelservice-decode   0%/1         1         16         1         37m
```

### Cleanup

To remove the autoscaling configuration, simply delete the kustomize overlay:

```bash
kubectl delete -k inference-scheduling-autoscaling/ -n ${NAMESPACE}
```

## WVA Controller Cleanup

You can use helm to uninstall WVA:

```bash
helm uninstall llm-d-wva -n ${NAMESPACE}
```

If you installed Prometheus Adapter for WVA, you can uninstall it as well:

```bash
helm uninstall prometheus-adapter -n ${MON_NS:-llm-d-monitoring}
```

## Advanced Configuration, Updates, and Troubleshooting

Please refer to the [Workload Variant Autoscaler documentation](https://github.com/llm-d-incubation/workload-variant-autoscaler) for advanced configuration options, updating WVA versions, and troubleshooting tips.

## Install Prometheus Adapter (Required Dependency)

Choose your platform and follow the corresponding section:

### On OpenShift

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=openshift-user-workload-monitoring

# Download OpenShift-specific values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.5.1/config/samples/prometheus-adapter-values-ocp.yaml

# Update Prometheus URL
sed -i.bak "s|url:.*|url: https://thanos-querier.openshift-monitoring.svc.cluster.local|" ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml || \
  echo "Edit ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml to set prometheus.url"

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml

# Verify RBAC permissions
kubectl auth can-i --list --as=system:serviceaccount:${MON_NS}:prometheus-adapter | grep -E "monitoring.coreos.com|prometheuses|namespaces"

# Create ClusterRole for Prometheus API access if needed
kubectl apply -f - <<'YAML'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: allow-thanos-querier-api-access
rules:
- nonResourceURLs: [/api/v1/query, /api/v1/query_range, /api/v1/labels, /api/v1/label/*/values, /api/v1/series, /api/v1/metadata, /api/v1/rules, /api/v1/alerts]
  verbs: [get]
- apiGroups: [monitoring.coreos.com]
  resourceNames: [k8s]
  resources: [prometheuses/api]
  verbs: [get, create, update]
- apiGroups: [""]
  resources: [namespaces]
  verbs: [get]
YAML
```

### On GKE/Generic Kubernetes

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.5.1/config/samples/prometheus-adapter-values.yaml

# Update Prometheus URL
sed -i.bak "s|url:.*|url: http://llmd-kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local:9090|" ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml || \
  echo "Edit ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml to set prometheus.url"

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml
```

### On Kind/HTTPS Prometheus

For Kind clusters with HTTPS Prometheus (configured in Step 2), the `prometheus-ca` ConfigMap is created by WVA (Step 5). Configure Prometheus Adapter to use it:

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.5.1/config/samples/prometheus-adapter-values.yaml

# Configure values with CA cert (ConfigMap created by WVA in Step 5)
cat >> ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml <<EOF
prometheus:
  url: https://llmd-kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local
  port: 9090
extraArguments:
  - --prometheus-ca-file=/etc/ssl/certs/prometheus-ca.crt
extraVolumeMounts:
  - name: prometheus-ca
    mountPath: /etc/ssl/certs/prometheus-ca.crt
    subPath: ca.crt
    readOnly: true
extraVolumes:
  - name: prometheus-ca
    configMap:
      name: prometheus-ca
EOF

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml
```

> **Note**: WVA creates the `prometheus-ca` ConfigMap in the monitoring namespace using the `caCert` value. This ConfigMap is required for Prometheus Adapter (Step 6.3).

**Verify installation**: `kubectl get pods -n ${MON_NS} -l app.kubernetes.io/name=prometheus-adapter`


## FAQ

**Q: How do I know which external metrics provider (Prometheus Adapter vs KEDA) is used?**

A: run this command and check the output:

```bash
kubectl get apiservice v1beta1.external.metrics.k8s.io -o yaml
```
