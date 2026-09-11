# Fast Model Actuation + KEDA Autoscaling

[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-keda-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-keda-ibm-acc-gpu-vllm-x.yaml)

## Overview

This guide combines [Fast Model Actuation (FMA)](../fast-model-actuation/README.md) with **scale-from-zero autoscaling via KEDA**. A KEDA `ScaledObject` scales the FMA server-requesting Deployment (`fma-requester`) on Endpoint Picker (EPP) flow-control metrics — all the way down to **zero** when the pool is idle, and back up on the first queued request. Each requesting pod reserves a GPU and drives the FMA controllers to bring a vLLM instance online via a **hot or warm start**; scaling to zero releases the GPU and puts the vLLM to sleep. (See the [FMA guide](../fast-model-actuation/README.md#overview) for what hot and warm start mean.)

## Configuration

| Parameter                | Value                                                        |
| ------------------------ | ------------------------------------------------------------ |
| Model                    | [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)    |
| Requester replicas       | 0 (KEDA floor, idle) … 2 (KEDA ceiling)                      |
| Launcher count           | 1 (per matching GPU node)                                    |
| GPUs per requester pod   | 1                                                            |
| Scale metric             | `llm_d_epp_flow_control_queue_size` (threshold `1`, activation `0`) |
| Autoscaler               | KEDA `ScaledObject` → HPA on `fma-requester` (scale-from-zero) |
| Router                   | llm-d-router-standalone (EPP `flowControl` enabled)          |

## Prerequisites

This guide assumes you have a Kubernetes cluster with GPU nodes, the [llm-d router](../../guides/recipes/router/README.md) infrastructure, and a **Prometheus/monitoring stack that scrapes the EPP**. If you are starting from an existing llm-d deployment, the Gateway API Inference Extension CRDs may already be installed and you can skip that step.

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- **Monitoring stack with Prometheus over HTTPS** — See [autoscaling prerequisites](../workload-autoscaling/README.md#prerequisites) and [Prometheus Setup Guide](../../docs/operations/observability/setup.md). This includes [KEDA installation](../workload-autoscaling/README.md#kubernetes-metrics-adapter).

- **EPP flow control enabled** — The `llm_d_epp_flow_control_queue_size` metric KEDA scales on requires the EPP flow control feature gate; it is also what makes an incoming request enqueue (rather than fail fast) when there is no ready backend yet, which is what lets this guide scale from zero. This guide's router values enable the `flowControl` gate for you (applied in [step 4](#4-deploy-the-llm-d-router-epp-flow-control-enabled)), so no extra action is needed. See [EPP Flow Control](../../docs/architecture/core/router/epp/flow-control.md) for details on flow control behavior.

- Checkout llm-d repo:

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

- Set the guide specific environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=fast-model-actuation-keda
export NAMESPACE=llm-d-fast-model-actuation-keda
export MONITORING_NAMESPACE=llm-d-monitoring
export FMA_VERSION=0.6.5
export FMA_CHART_INSTANCE_NAME=fma
export MODEL=Qwen/Qwen3-0.6B
export CURL_TEST_IMAGE=cfmanteiga/alpine-bash-curl-jq:latest
export BENCHMARK_REF=main
export HARNESS=inference-perf
export WORKLOAD=shared_prefix_synthetic_heavy.yaml
export GATEWAY_CLASS=epponly # options: epponly, gke, agentgateway, istio
```
<!-- guide:env.static end -->

- Source the common guide environment variables (`GAIE_VERSION`, `ROUTER_CHART_VERSION`, `ROUTER_STANDALONE_CHART`, …):

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

> [!NOTE]
> Some environment variables are common amongst guides. Inspect the file sourced above so the rest of the guide makes sense.

- Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

- Confirm KEDA is installed:

<!-- guide:prerequisites.keda start -->
```bash
# KEDA (or, on OpenShift, the Custom Metrics Autoscaler Operator) must be
# installed cluster-wide before this guide runs.
kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1 \
  || { echo "KEDA not installed -- see https://keda.sh/docs/latest/deploy/"; exit 1; }
```
<!-- guide:prerequisites.keda end -->

- Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

> [!NOTE]
> **Fast Model Actuation** — This guide reuses the base [Fast Model Actuation guide](../fast-model-actuation/README.md)'s manifests (`LauncherConfig`, `LauncherPopulationPolicy`, the requester Deployment) as a kustomize base and stands up its own FMA controllers. You do not need to deploy it separately.

## Installation Instructions

At minimum, the user running these commands needs rights to create and manage CRDs, ClusterRoles, ClusterRoleBindings, KEDA `ScaledObject`s, and Helm releases across namespaces.

### 1. Apply FMA CRDs

<!-- guide:deploy.fma_crds start -->
```bash
kubectl apply --server-side \
  -f "https://raw.githubusercontent.com/llm-d-incubation/llm-d-fast-model-actuation/v${FMA_VERSION}/config/crds.yaml"
kubectl wait --for=condition=Established crd/inferenceserverconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherpopulationpolicies.fma.llm-d.ai --timeout=120s
```
<!-- guide:deploy.fma_crds end -->

### 2. Grant RBAC Permissions

The FMA controllers need cluster-level access to list nodes (for the launcher-populator) and namespace-level access for launcher pods to patch their own pod state. In addition, KEDA needs a metrics-reader ServiceAccount bound to `cluster-monitoring-view` so OpenShift's Thanos Querier authorizes its trigger queries. (The EPP `/metrics` scrape needs no RBAC here — it is handled by the router chart's ServiceMonitor, enabled unauthenticated at router install in [step 4](#4-deploy-the-llm-d-router-epp-flow-control-enabled).)

<!-- guide:deploy.rbac start -->
```bash
# ClusterRole (cluster-scoped): the FMA controllers list nodes for the launcher-populator.
# Reused verbatim from the base guide (identical RBAC).
kubectl apply -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/clusterrole.yaml

# ServiceAccount + Role (namespaced): the launcher pods run as the
# fma-launcher ServiceAccount so the state-change-reflector sidecar can
# patch its own pod (the dual-pods.llm-d.ai/vllm-instance-signature
# annotation). Reused verbatim from the base guide.
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/role.yaml

# RoleBinding (namespaced): bind the Role to the fma-launcher ServiceAccount.
# Created imperatively (not from a static manifest) so ${NAMESPACE} drives both the
# binding's namespace and the subject ServiceAccount's namespace.
kubectl create rolebinding fma-launcher-pod-state-writer \
  --role=fma-launcher-pod-state-writer \
  --serviceaccount=${NAMESPACE}:fma-launcher \
  -n ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding (cluster-scoped): grant the KEDA metrics-reader
# ServiceAccount cluster-monitoring-view, the role Thanos Querier requires
# to answer KEDA's trigger queries.
kubectl create clusterrolebinding "keda-epp-metrics-reader-monitoring-view-${NAMESPACE}" \
  --clusterrole=cluster-monitoring-view \
  --serviceaccount=${NAMESPACE}:keda-epp-metrics-reader \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:deploy.rbac end -->

> [!NOTE]
> Only the `fma-node-viewer` **ClusterRole** is created here. The matching **ClusterRoleBinding** is created by the FMA Helm chart in the next step, via `--set global.nodeViewClusterRole=fma-node-viewer`.

### 3. Deploy FMA Controllers via Helm

<!-- guide:deploy.fma_controllers start -->
```bash
helm upgrade --install ${FMA_CHART_INSTANCE_NAME} \
  oci://ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/charts/fma-controllers \
  --version ${FMA_VERSION} \
  --set global.nodeViewClusterRole=fma-node-viewer \
  -n ${NAMESPACE}

kubectl wait --for=condition=available --timeout=180s \
  deployment "${FMA_CHART_INSTANCE_NAME}-dual-pods-controller" -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s \
  deployment "${FMA_CHART_INSTANCE_NAME}-launcher-populator" -n ${NAMESPACE}
```
<!-- guide:deploy.fma_controllers end -->

### 4. Deploy the llm-d Router (EPP flow-control enabled)

The router values for this guide enable the EPP `flowControl` feature gate so the flow-control queue-depth metric is emitted for KEDA to scale on (and so requests enqueue when no backend is ready yet):

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 5. Deploy the Model Server (Dual Pods)

Apply the FMA custom resources — `InferenceServerConfig`, `LauncherConfig`, and `LauncherPopulationPolicy` — **together with** the server-requesting `fma-requester` Deployment in a single `kubectl apply -k modelserver/`. This guide's `modelserver/` reuses the base [`fast-model-actuation`](../fast-model-actuation) guide's manifests as a kustomize base — so the shared FMA plumbing (the `InferenceServerConfig`, `LauncherConfig`, `LauncherPopulationPolicy`, and the requester Deployment, all serving Qwen3-0.6B) is maintained once — and patches only what this guide changes: the `llm-d.ai/guide` label, and the requester applied at 1 replica so the stack comes up serving. KEDA takes ownership of the replica count in the [next step](#6-enable-scale-from-zero-autoscaling-keda), scaling the requester to 0 once the flow-control queue is empty and back to 1 on the next queued request:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/

kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=300s
```
<!-- guide:deploy.modelserver end -->

### 6. Enable Scale-from-Zero Autoscaling (KEDA)

Apply the KEDA layer — the `ScaledObject`, its `TriggerAuthentication`, and the
shared metrics-reader ServiceAccount + token — from the `ocp` overlay, then wait
for the `ScaledObject` to reconcile `Ready`. The overlay mirrors the
[`keda-epp-queue`](../workload-autoscaling/keda-epp-queue) sibling: it points the
queue-depth trigger at Thanos Querier and enables bearer auth, and the service-ca
operator injects the Thanos CA (`service-ca.crt`) into the token Secret
automatically, so **no `prometheus-token` copy is required** on OpenShift. The one
cluster-scoped binding KEDA needs (`cluster-monitoring-view`, so Thanos Querier
authorizes the trigger queries) was created imperatively in the
[RBAC step](#2-grant-rbac-permissions), where `${NAMESPACE}` drives the subject
namespace — the namespace-agnostic overlay cannot carry it. Once the
`ScaledObject` is `Ready`, KEDA creates the HPA and begins owning the requester's
replica count:

<!-- guide:deploy.keda start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/keda/ocp

kubectl wait --for=condition=Ready --timeout=120s \
  scaledobject/fast-model-actuation-keda-queue -n ${NAMESPACE}
```
<!-- guide:deploy.keda end -->

> [!NOTE]
> **Generic Kubernetes (non-OpenShift).** The command above uses the `ocp`
> overlay, which targets OpenShift's Thanos Querier and its service-ca-provisioned
> token. On generic Kubernetes with the bundled llm-d kube-prometheus-stack, apply
> the `keda/k8s` overlay instead — its base `TriggerAuthentication` does CA-only
> auth against the bundled Prometheus service, so first copy that stack's CA into
> the workload namespace as the `keda-prometheus-auth` Secret. The
> `cluster-monitoring-view` binding and the metrics-reader ServiceAccount token are
> OpenShift-specific and are not needed here.

<!-- llm-d-cicd:skip start -->
```bash
# Generic Kubernetes only (do NOT run on OpenShift):
kubectl create secret generic keda-prometheus-auth \
  --namespace ${NAMESPACE} \
  --from-literal=ca.crt="$(kubectl get configmap prometheus-web-tls-ca \
    -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}')" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/keda/k8s
```
<!-- llm-d-cicd:skip end -->

## Verification

### 1. Get the IP of the Router

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send a Test Request

Send a completion request through the EPP. The stack comes up serving, so this normally returns right away; it is wrapped in a retry loop because KEDA may already have begun scaling the idle requester toward zero, in which case this request is itself the demand signal that wakes it (see the next section):

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --pod-running-timeout=180s \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c '
    set -e
    i=0
    while [ $i -lt 12 ]; do
      code=$(curl -s -o /tmp/b -w "%{http_code}" --max-time 90 \
        -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\", \"max_tokens\": 5}" || echo 000)
      echo "attempt ${i}: HTTP ${code}"
      if [ "${code}" = "200" ]; then cat /tmp/b; echo; jq -e ".choices[0].text | length > 0" /tmp/b >/dev/null; exit 0; fi
      i=$((i+1)); sleep 15
    done
    echo "no 200 after retries"; cat /tmp/b 2>/dev/null || true; exit 1'
```
<!-- guide:verify.tests.request end -->

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking. The commands below run the `inference-perf` harness with the `shared_prefix_synthetic_heavy.yaml` workload. On this small model (Qwen3-0.6B) each request drains in tens of milliseconds, so the flow-control queue does not stay backed up — the value you are exercising is the **scale-from-zero transition**: when the requester has scaled to 0, the benchmark's first arrivals enqueue, KEDA's activation trip on that cold-start queue transient wakes the pool (0→1), and the arrival stream keeps it warm for the run. This exercises the wake-from-zero and cooldown-to-zero behavior rather than a sustained multi-replica scale-out; the richer, FMA/KEDA-specific experimentation workflow lives in [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) itself.

> [!IMPORTANT]
> The Benchmarking section below contains only the **guide-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).

### 1. Install the CLI

<!-- guide:benchmark.setup start -->
```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/${BENCHMARK_REF}/install.sh | bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```
<!-- guide:benchmark.setup end -->

> [!NOTE]
> Subsequent `llmdbenchmark` commands assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the commands above.

### 2. Resolve the endpoint of the stack you just deployed

<!-- guide:benchmark.endpoint.standalone start -->
```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
```
<!-- guide:benchmark.endpoint.standalone end -->

### 3. Run the benchmark

<!-- guide:benchmark.execute start -->
```bash
llmdbenchmark \
  --spec           guides/${GUIDE_NAME} \
  run \
  --endpoint-url   "${ENDPOINT_URL}" \
  --gateway-class  "${GATEWAY_CLASS}" \
  --model          "${MODEL}" \
  --namespace      "${NAMESPACE}" \
  --harness        "${HARNESS}" \
  --workload       "${WORKLOAD}" \
  --analyze
```
<!-- guide:benchmark.execute end -->

## Cleanup

To remove all deployed components:

> [!WARNING]
> **Order matters.** Delete the KEDA `ScaledObject` first (so the HPA stops scaling the requester), then the model-server CRs and the requester Deployment, then wait for pods to drain while the dual-pods controller is still running to strip their finalizers, and only then remove the controllers. Removing the controller before the pods drain leaves finalizer-bound pods stuck in `Terminating` forever.

<!-- guide:cleanup start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/keda/ocp --ignore-not-found=true

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/ --ignore-not-found=true

kubectl wait --for=delete pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s

kubectl wait --for=delete pod -l app.kubernetes.io/component=launcher -n ${NAMESPACE} --timeout=120s

helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/role.yaml --ignore-not-found=true

kubectl delete rolebinding fma-launcher-pod-state-writer -n ${NAMESPACE} --ignore-not-found=true

kubectl delete clusterrolebinding "keda-epp-metrics-reader-monitoring-view-${NAMESPACE}" --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/clusterrole.yaml --ignore-not-found=true

kubectl delete namespace ${NAMESPACE}

kubectl delete crd inferenceserverconfigs.fma.llm-d.ai launcherconfigs.fma.llm-d.ai launcherpopulationpolicies.fma.llm-d.ai
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->
