# KEDA + EPP Pool-Level Saturation Metrics

KEDA queries Prometheus directly for two EPP-emitted, InferencePool-scoped signals and scales the model server `Deployment` accordingly. No WVA controller, no Prometheus Adapter — just KEDA, Prometheus, and your model servers.

## Metrics

| Metric | Type | Description | Label |
|---|---|---|---|
| `llm_d_epp_flow_control_pool_saturation` | Gauge | Pool saturation level (0.0 to 1.0+). Values above 1.0 indicate the pool is overloaded and throttling requests. | `inference_pool` |
| `inference_objective_running_requests` | Gauge | Current number of active in-flight requests across the pool. | `model_name` |

For details on these metrics, see:
- [EPP Flow Control Metrics](../../docs/architecture/core/router/epp/flow-control.md#metrics--observability)
- [EPP Request Handling Metrics](../../docs/architecture/core/router/epp/request-handling.md)

## Prerequisites

Before proceeding, ensure you have:

1. **Monitoring stack with Prometheus over HTTPS** — See [autoscaling prerequisites](README.md#prerequisites) and [Prometheus Setup Guide](../../docs/operations/observability/setup.md).

2. **KEDA installed in your cluster** — Follow the [KEDA deployment guide](https://keda.sh/docs/2.20/deploy/).

   > [!NOTE]
   > On OpenShift, use the [Custom Metrics Autoscaler Operator](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/automatically-scaling-pods-with-the-custom-metrics-autoscaler-operator).

3. **Optimized-baseline deployment** — Complete the [optimized-baseline guide](../optimized-baseline/README.md).

## Set Namespaces

```bash
# Namespace where your inference deployment is running
export NAMESPACE=llm-d-optimized-baseline

# Namespace where the monitoring stack (Prometheus) was installed
export MONITORING_NAMESPACE=llm-d-monitoring

export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

## Configure

### 1. Extract Prometheus CA Certificate and Create Secret

```bash
# Extract Prometheus CA cert
PROMETHEUS_CA_CERT=$(kubectl get secret prometheus-web-tls -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.tls\.crt}' | base64 -d)

# Create generic secret with the CA cert for KEDA to access Prometheus API securely
kubectl create secret generic prometheus-auth \
  --from-literal=ca.crt="${PROMETHEUS_CA_CERT}" \
  --dry-run=client -o yaml | kubectl apply -f - -n ${NAMESPACE}
```

### 2. Apply KEDA ScaledObject and TriggerAuthentication

For **Kubernetes (generic)**:
```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation -n ${NAMESPACE}
```

For **OpenShift**:
```bash
kubectl apply -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation/overlays/ocp -n ${NAMESPACE}
```

Before applying, edit the base manifests to match your deployment:
- `scaledobject.yaml`: Update `inference_pool` label in the PromQL queries, `minReplicaCount`, `maxReplicaCount`, and thresholds for each trigger.
- If using the base k8s config, also update `<prometheus-url>` in `serverAddress` to point to your Prometheus instance.
- `triggerauthentication.yaml`: Verify the bearer token Secret contains valid credentials for Prometheus access.

## Verify

Check that the ScaledObject is ready and KEDA has created its HPA:

```bash
kubectl get scaledobject -n ${NAMESPACE}
kubectl get hpa -n ${NAMESPACE}
```

Expected output:

```
NAME                                    READY   ACTIVE   AGE
optimized-baseline-nvidia-gpu-vllm      True    True     1m

NAME                                    REFERENCE                                      TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
keda-hpa-optimized-baseline-nvidia-gpu  Deployment/optimized-baseline-nvidia-gpu       0%, 0%          1         10        1          1m
```

> [!NOTE]
> KEDA creates its own HPA object from the ScaledObject. Do **not** apply a separate `hpa.yaml` — doing so will cause conflicts.

## Cleanup

```bash
kubectl delete -k ${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-saturation -n ${NAMESPACE}
kubectl delete secret prometheus-auth -n ${NAMESPACE}
```
