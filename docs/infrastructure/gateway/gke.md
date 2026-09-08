# GKE

This guide shows how to deploy llm-d with
[GKE Gateway](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway) as your inference gateway. By the end, inference requests will be forwarded by a GKE-managed `Gateway` to your model servers via the llm-d EPP.

> [!NOTE]
> This guide assumes familiarity with
> [Gateway API](https://gateway-api.sigs.k8s.io/) and llm-d.

## Prerequisites

1. You have verified the version in and sourced in the llm-d repo wide `env.sh` file:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
```

1. The following steps from the [GKE Inference Gateway deployment documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway) should be run:

* [Verify your prerequisites](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#before-you-begin)
* [Configure a proxy-only subnet](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#configure_a_proxy-only_subnet)
* [Enable Gateway API in your cluster](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#enable-gateway)
* [Verify your cluster](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#verify-internal)

## Step 1: Install Gateway API and Gateway API Inference Extension CRDs

> [!NOTE]
> GKE automatically installs all GA CRDs for Gateway API and Gateway API Inference Extension on GKE versions `1.34.0-gke.1626000` or later. If using this version or newer, skip to Step 2.

For GKE versions earlier than `1.34.0-gke.1626000`, install the CRDs manually:

```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```

Verify the APIs are available:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

## Step 2: Deploy the Gateway

GKE supports two types of Application Load Balancers. Choose the one that matches your network requirements:

### Regional External Application Load Balancer

For internet-facing workloads accessible from outside your VPC:

```bash
LLM_D_VERSION=main  # Use 'main' for latest, or a release tag like 'v0.7.0'
GATEWAY_CLASS=gke-l7-regional-external-managed
kubectl apply -n ${NAMESPACE} -k "https://github.com/llm-d/llm-d/guides/recipes/gateway/${GATEWAY_CLASS}?ref=${LLM_D_VERSION}"
```

### Regional Internal Application Load Balancer

For private workloads accessible only within your VPC:

```bash
LLM_D_VERSION=main  # Use 'main' for latest, or a release tag like 'v0.7.0'
GATEWAY_CLASS=gke-l7-rilb
kubectl apply -n ${NAMESPACE} -k "https://github.com/llm-d/llm-d/guides/recipes/gateway/${GATEWAY_CLASS}?ref=${LLM_D_VERSION}"
```

## Step 3: Verify the Gateway

Verify the `Gateway` is programmed:

```bash
kubectl get gateway -n ${NAMESPACE} llm-d-inference-gateway
```

Expected output:

```text
NAME                      CLASS                              ADDRESS         PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   xx.xx.xx.xx     True         30s
```

Wait until `PROGRAMMED` shows `True` before proceeding.

## Step 4: Deploy a Model Serving Workload

Before sending inference requests through the Gateway, you must deploy a model server (such as vLLM), an `InferencePool`, and an `HTTPRoute` connecting the Gateway to the pool. You can deploy using a standard baseline or enable High Availability.

### Option A: Standard Deployment (Well-Lit Path)

Deploy one of the well-lit path guides with the GKE Gateway provider enabled:

1. **Deploy the llm-d Router in Gateway mode:**

```bash
export PROVIDER_NAME=gke
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${PROVIDER_NAME} \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

2. **Deploy the model server with the GKE overlay:**

```bash
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, hpu, tpu/v6, tpu/v7, cpu
export MODEL_SERVER=vllm    # options: vllm, sglang, trtllm
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/gke/
```

### Option B: High Availability with Preferred Backends
Deploying Endpoint Picker (EPP) with [GKE Preferred Backends](https://docs.cloud.google.com/load-balancing/docs/service-lb-policy#preferred-backends) enables an active-passive routing topology backed by Cloud Load Balancing. Steady-state ext_proc traffic routes exclusively to the primary EPP replica (epp-0) to keep KV-cache tracking and request state centralized. If epp-0 becomes unhealthy, Cloud Load Balancing shifts traffic to the warm standby replica (epp-1), with InferencePool fail-open mode providing an additional safety net against dropped requests.

#### Key Benefits
* **State Consistency**: Directs 100% of steady-state `ext_proc` traffic to the primary EPP instance (`epp-0`), preserving KV-cache state and request scheduling context.
* **Zero-Downtime Failover**: If the primary EPP pod crashes or undergoes maintenance, Cloud Load Balancer detects the failure via active gRPC health checks and immediately routes traffic to the warm standby replica (`epp-1`).
* **Fail-Open Resilience**: Paired with `failureMode: FailOpen` on the `InferencePool`, transient routing blips bypass EPP and forward directly to model servers without dropping user requests.

#### 1. **Deploy the llm-d Router with Preferred Backends enabled:**
> [!NOTE]
> When Preferred Backends is enabled, the chart deploys a single StatefulSet where pod ordinals map to target priority tiers and generates corresponding GCPBackendPolicy resources for each tier.

```bash
export PROVIDER_NAME=gke
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${PROVIDER_NAME} \
  --set provider.gke.preferredBackends.enabled=true \
  --set provider.gke.preferredBackends.preferredReplicas=1 \
  --set provider.gke.preferredBackends.defaultReplicas=1 \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Key configuration parameters (see [`values.yaml`](https://github.com/llm-d/llm-d-router/blob/main/config/charts/llm-d-router-gateway/values.yaml)):
* `preferredReplicas` (Default: `1`): the replica count for active primary pod ordinals pinned to the `PREFERRED` backend preference tier (`epp-0`). Setting a value greater than 1 scales concurrent active routing capacity.
* `defaultReplicas` (Default: `1`): the replica count for standby pod ordinals pinned to the `DEFAULT` backend preference tier (`epp-1`). Setting a value greater than 1 scales warm standby failover capacity.
* `balancingMode` (Default: `RATE`): the [calculation mode](https://docs.cloud.google.com/load-balancing/docs/backend-service#traffic_distribution) used to determine load thresholds (`RATE`, `UTILIZATION`, or `CONNECTION`).
* `maxRatePerEndpoint` (Default: `100`): the maximum requests per second per pod instance before spilling over to standby tiers.
* `capacityScalerPercent` (Default: `100`): the effective capacity ceiling percentage (0 to 100).

#### 2. **Deploy the model server with the GKE overlay:**

```bash
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, hpu, tpu/v6, tpu/v7, cpu
export MODEL_SERVER=vllm    # options: vllm, sglang, trtllm
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/gke/
```

#### 3. **Discover and attach the primary Preferred Network Endpoint Groups (NEGs) across zones to the generated Google Cloud `BackendService`:**

> [!NOTE]
> The `InferencePool` resource natively targets the standby backup Service (`Service/${GUIDE_NAME}-epp-backup`).
> Anchoring declarative Gateway ownership to the standby Service ensures that GKE Gateway Controller reconciliation loops do not detach manually attached primary NEGs during pod restarts or updates.

```bash
export PROJECT="<your GCP project>"
export REGION="<your GCP region>"
export NAMESPACE="<your namespace>"
export GUIDE_NAME="<llm-d guide name>"

export BACKEND_SERVICE=$(gcloud compute backend-services list --project=${PROJECT} --format="value(name)" | grep "${GUIDE_NAME}-epp")

export PRIMARY_NEG=$(kubectl get svc ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.metadata.annotations.cloud\.google\.com/neg-status}' | jq -r '.network_endpoint_groups["9002"]')

for ZONE in $(kubectl get svc ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.metadata.annotations.cloud\.google\.com/neg-status}' | jq -r '.zones[]'); do
  gcloud compute backend-services add-backend ${BACKEND_SERVICE} \
    --network-endpoint-group=${PRIMARY_NEG} \
    --network-endpoint-group-zone=${ZONE} \
    --region=${REGION} \
    --project=${PROJECT} \
    --balancing-mode=RATE \
    --max-rate-per-endpoint=100
  sleep 15
done
```

> [!IMPORTANT]
> Once zonal NEGs are attached to the Google Cloud `BackendService`, scaling replica counts (`preferredReplicas` or `defaultReplicas`) or restarting pods does not require running `gcloud` commands again. The GKE NEG Controller automatically synchronizes individual pod IP endpoints into the registered zonal NEGs.
>
> If you delete and recreate the parent `Gateway` or `InferencePool` resource, the GKE Gateway Controller recreates the underlying Google Cloud `BackendService` with a new cloud identifier. In that event, execute the attachment workflow above to attach primary zonal NEGs to the newly created `BackendService`.

## Step 5: Send a Request

Get the `Gateway` external address:

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

Send an inference request via the managed `Gateway`:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": '\"${MODEL_NAME}\"',
        "prompt": "How are you today?"
    }' | jq
```

## Cleanup

```bash
kubectl delete gateway llm-d-inference-gateway -n ${NAMESPACE}
```

## Troubleshooting

### Gateway not showing `PROGRAMMED=True`

```bash
kubectl describe gateway llm-d-inference-gateway -n ${NAMESPACE}
```

Verify all prerequisites were applied, especially [enabling Gateway API in your cluster](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#enable-gateway) and [configuring a proxy-only subnet](https://cloud.google.com/kubernetes-engine/docs/how-to/deploying-gateways#configure_a_proxy-only_subnet). Also make sure the cluster is running [a supported GKE version](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#gateway-controller-requirements).

### HTTPRoute not accepted

```bash
kubectl describe httproute ${GUIDE_NAME} -n ${NAMESPACE}
```

Verify that `parentRefs` matches the Gateway name and `backendRefs` matches the InferencePool name.

### No response from Gateway IP

```bash
kubectl get gateway llm-d-inference-gateway  -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}'
```

If the address is empty, your Gateway may still be waiting for a LoadBalancer service. Check that your cluster supports external load balancers.

### Getting `fault filter abort` response

A couple of issues may cause this:

1. The request doesn't match the routing rules setup on HTTPRoute.
2. A misconfiguration in the gateway's backend routing. When configured correctly, the HTTPRoute status should have a condition of type `Reconciled` and reason `ReconciliationSucceeded`.
