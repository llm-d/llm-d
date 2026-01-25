# Autoscaling with Workload Variant Autoscaler (WVA)

The [Workload Variant Autoscaler](https://github.com/llm-d-incubation/workload-variant-autoscaler/tree/v0.4.1) (WVA) provides dynamic autoscaling capabilities for llm-d inference deployments, automatically adjusting replica counts based on inference server saturation.

## Overview

WVA integrates with llm-d to:

- Dynamically scale inference replicas based on workload saturation
- Optimize resource utilization by adjusting to traffic patterns
- Reduce tail latency through saturation-based scaling decisions

> **Note**: WVA currently supports only the [Intelligent Inference Scheduling](../inference-scheduling/README.md) well-lit path. Other well-lit paths (such as Prefill/Decode Disaggregation or Wide Expert-Parallelism) are not currently supported.

## Prerequisites

Before installing WVA, ensure you have:

1. **Kubernetes cluster**: A running Kubernetes cluster (v1.31+) with GPU support. WVA uses the [Intelligent Inference Scheduling](../inference-scheduling/README.md) well-lit path, which requires GPUs. See [Hardware Requirements](../inference-scheduling/README.md#hardware-requirements) for supported accelerator types. If you need to set up a local cluster:
   - **Kind**: For Kind clusters with GPU emulation, use the [WVA Kind setup script](https://github.com/llm-d-incubation/workload-variant-autoscaler/blob/v0.4.1/deploy/kind-emulator/setup.sh) which creates a cluster and patches nodes with GPU capacity (required for pod scheduling if using GPU-requesting pods). **Note**: Saturation-based scaling does not require node patching; it only uses workload metrics. See [Infrastructure Prerequisites](../prereq/infrastructure/README.md) for other cluster setup options.
   - **Minikube**: See [Minikube setup documentation](../../docs/infra-providers/minikube/README.md) for single-host development.
   - **Production clusters**: See [Infrastructure Prerequisites](../prereq/infrastructure/README.md) for provider-specific setup (GKE, AKS, OpenShift (4.18+), etc.).

2. **Gateway control plane**: Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md) (Istio) before installation.

3. **Prometheus monitoring stack**: WVA requires Prometheus to be accessible for metric collection. **WVA requires HTTPS connections to Prometheus**. The monitoring setup depends on your platform:
   - **OpenShift**: User Workload Monitoring should be enabled (see [OpenShift monitoring docs](https://docs.redhat.com/en/documentation/monitoring_stack_for_red_hat_openshift/4.18/html-single/configuring_user_workload_monitoring/index))
   - **GKE**: An in-cluster Prometheus instance is required (GMP does not expose HTTP API). See [GKE configuration](#gke) below for setup instructions.
   - **Kind/Minikube**: Install Prometheus with TLS/HTTPS configuration. See [Kind/Minikube configuration](#other-kubernetes-platforms-kind-minikube-etc) below for installation and TLS setup instructions.
   - **Other Kubernetes**: A Prometheus stack must be installed with HTTPS support (see [monitoring documentation](../../docs/monitoring/README.md))

4. **Create Installation Namespace**:

  ```bash
  export NAMESPACE=llm-d-autoscaler
  kubectl create namespace ${NAMESPACE}
  ```

1. **HuggingFace token secret**: [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.

## Installation

The workload-autoscaling helmfile installs the complete llm-d [Intelligent Inference Scheduling](../inference-scheduling/README.md) stack (infra, gaie, modelservice) plus WVA in a single `helmfile apply` command. **Install Prometheus Adapter separately in [Step 6](#step-6-install-prometheus-adapter-required-dependency) after WVA installation.**

### Step 1: Configure WVA Values

Edit `workload-autoscaling/values.yaml` to configure WVA settings (controller, Prometheus, accelerator, SLOs, HPA). Set namespace:

```bash
export NAMESPACE=llm-d-autoscaler
```

### Step 2: Platform-Specific Configuration

Update `workload-autoscaling/values.yaml` with platform-specific settings:

#### OpenShift

For OpenShift deployments, update the values file:

```yaml
wva:
  prometheus:
    monitoringNamespace: openshift-user-workload-monitoring
    baseURL: "https://thanos-querier.openshift-monitoring.svc.cluster.local:9091"
    serviceAccountName: "prometheus-k8s"
    tls:
      insecureSkipVerify: true # or "false" for production
      caCertPath: "" # or set ca cert path for production - "/etc/ssl/certs/prometheus-ca.crt"
```

Extract CA cert (if required): `kubectl get secret thanos-querier-tls -n openshift-monitoring -o jsonpath='{.data.tls\.crt}' | base64 -d > ${TMPDIR:-/tmp}/prometheus-ca.crt`

#### GKE

GMP doesn't expose HTTP API. Deploy in-cluster Prometheus:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n llm-d-monitoring --create-namespace
```

Update `workload-autoscaling/values.yaml`:

```yaml
wva:
  prometheus:
    monitoringNamespace: llm-d-monitoring
    baseURL: "http://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090"
    tls:
      insecureSkipVerify: true
```

#### Other Kubernetes Platforms (Kind, Minikube, etc.)

> **WVA HTTPS Requirement**: WVA **requires** HTTPS for Prometheus connections. The default Prometheus installation on Kind/Minikube uses HTTP only. You **must** install Prometheus and configure it with TLS.

**Install Prometheus**:

```bash
export MON_NS=llm-d-monitoring

# Install Prometheus stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install llmd prometheus-community/kube-prometheus-stack -n ${MON_NS} --create-namespace
```

**Enable TLS on Prometheus** (Required):

WVA requires HTTPS for Prometheus. Configure Prometheus with TLS:

```bash
export MON_NS=llm-d-monitoring

# Create self-signed TLS certificate
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout ${TMPDIR:-/tmp}/prometheus-tls.key -out ${TMPDIR:-/tmp}/prometheus-tls.crt -days 365 \
  -subj "/CN=prometheus" \
  -addext "subjectAltName=DNS:llmd-kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local,DNS:llmd-kube-prometheus-stack-prometheus.${MON_NS}.svc,DNS:prometheus,DNS:localhost"

# Create TLS secret and upgrade Prometheus
kubectl create secret tls prometheus-web-tls --cert=${TMPDIR:-/tmp}/prometheus-tls.crt --key=${TMPDIR:-/tmp}/prometheus-tls.key -n ${MON_NS} --dry-run=client -o yaml | kubectl apply -f -

helm upgrade llmd prometheus-community/kube-prometheus-stack -n ${MON_NS} \
  --set prometheus.prometheusSpec.web.tlsConfig.cert.secret.name=prometheus-web-tls \
  --set prometheus.prometheusSpec.web.tlsConfig.cert.secret.key=tls.crt \
  --set prometheus.prometheusSpec.web.tlsConfig.keySecret.name=prometheus-web-tls \
  --set prometheus.prometheusSpec.web.tlsConfig.keySecret.key=tls.key \
  --reuse-values
```

> **Note**: For Kind clusters, consider using [simulated-accelerators](../simulated-accelerators/README.md) if vLLM GPU detection fails. **Saturation-based scaling does not require node patching**—it only uses workload metrics (KV cache utilization, queue length). Simulator pods don't request GPUs, so they can schedule without node patching. If using regular vLLM with GPU resource requests on Kind, you must patch nodes for pods to schedule (e.g., `kubectl patch node <node-name> -p '{"status":{"capacity":{"nvidia.com/gpu":"8"}}}'`). WVA automatically discovers its namespace via `POD_NAMESPACE`.

### Step 3: Create WVA Namespace (if needed)

The helmfile creates `llm-d-autoscaler` namespace automatically if it doesn't exist. **Note:** If you created the namespace in Prerequisites #4 for the HF token secret, it's already created.

**For OpenShift only**, ensure the namespace has the monitoring label:

```bash
export NAMESPACE=${NAMESPACE:-llm-d-autoscaler}
kubectl label namespace "${NAMESPACE}" openshift.io/user-monitoring=true --overwrite
```

### Step 4: Install WVA CRDs (Required)

Install WVA CRDs before deploying:

```bash
kubectl apply -f https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.4.1/charts/workload-variant-autoscaler/crds/llmd.ai_variantautoscalings.yaml
kubectl get crd variantautoscalings.llmd.ai
```

### Step 5: Install llm-d Stack with WVA

Install the complete llm-d inference-scheduling stack (infra, gaie, modelservice) plus WVA:

```bash
export NAMESPACE=${NAMESPACE:-llm-d-autoscaler}
cd guides/workload-autoscaling
helmfile apply -n ${NAMESPACE}
```

This installs the complete [Intelligent Inference Scheduling](../inference-scheduling/README.md) stack:

- **Infra** (gateway infrastructure)
- **GAIE** (inference pool and endpoint picker)
- **Model Service** (vLLM inference pods)
- **WVA** (workload-variant-autoscaler) in `llm-d-autoscaler` namespace

WVA automatically discovers its namespace via `POD_NAMESPACE`.

### Step 6: Install Prometheus Adapter (Required Dependency)

Prometheus Adapter exposes WVA's external metric to HPA/KEDA. Install **after** WVA installation (Step 5), which creates the required `prometheus-ca` ConfigMap.

Choose your platform and follow the corresponding section:

#### 6.1: OpenShift

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=openshift-user-workload-monitoring

# Download OpenShift-specific values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.4.1/config/samples/prometheus-adapter-values-ocp.yaml

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

#### 6.2: GKE/Generic Kubernetes

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.4.1/config/samples/prometheus-adapter-values.yaml

# Update Prometheus URL
sed -i.bak "s|url:.*|url: http://llmd-kube-prometheus-stack-prometheus.${MON_NS}.svc.cluster.local:9090|" ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml || \
  echo "Edit ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml to set prometheus.url"

# Install
helm upgrade -i prometheus-adapter prometheus-community/prometheus-adapter \
  --version 5.2.0 -n ${MON_NS} --create-namespace -f ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml
```

#### 6.3: Kind/HTTPS Prometheus

For Kind clusters with HTTPS Prometheus (configured in Step 2), the `prometheus-ca` ConfigMap is created by WVA (Step 5). Configure Prometheus Adapter to use it:

```bash
# Setup
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
export MON_NS=${MON_NS:-llm-d-monitoring}

# Download values
curl -o ${TMPDIR:-/tmp}/prometheus-adapter-values.yaml \
  https://raw.githubusercontent.com/llm-d-incubation/workload-variant-autoscaler/v0.4.1/config/samples/prometheus-adapter-values.yaml

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

### Step 7: Verify End-to-End Installation

```bash
export NAMESPACE=${NAMESPACE:-llm-d-autoscaler}
export MON_NS=${MON_NS:-llm-d-monitoring}

# Check pods
kubectl get pods -n ${MON_NS} -l app.kubernetes.io/name=prometheus-adapter
kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/name=workload-variant-autoscaler

# Verify metrics and HPA
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/inferno_desired_replicas" | jq
kubectl get hpa -n ${NAMESPACE}
kubectl get variantautoscalings -n ${NAMESPACE}
```

## Configuration Checklist

Edit `workload-autoscaling/values.yaml` for WVA settings. Key configurations:

**Model ID** (must match model configured in modelservice):

```yaml
llmd:
  modelID: "Qwen/Qwen3-0.6B"  # Must match model ID in ms-workload-autoscaling/values.yaml
```

**Accelerator** (L40S, A100, H100, Intel-Max-1550):

```yaml
va:
  accelerator: L40S # your accelerator type
```

**Prometheus** (platform-specific):

```yaml
wva:
  prometheus:
    monitoringNamespace: llm-d-monitoring
    baseURL: "https://..."  # Platform-specific URL
    tls:
      insecureSkipVerify: true
```

See [WVA chart documentation](https://github.com/llm-d-incubation/workload-variant-autoscaler/blob/v0.4.1/charts/workload-variant-autoscaler/README.md) for all options.

## Cleanup

Remove WVA and Prometheus Adapter:

```bash
# Remove WVA stack
cd guides/workload-autoscaling
helmfile destroy -n ${NAMESPACE:-llm-d-autoscaler}

# Remove Prometheus Adapter (if not needed by other components)
helm uninstall prometheus-adapter -n ${MON_NS:-llm-d-monitoring}
```

The llm-d stack (infra, gaie, modelservice) continues operating without autoscaling after WVA removal.
