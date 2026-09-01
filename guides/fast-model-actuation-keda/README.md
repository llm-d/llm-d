# Fast Model Actuation + KEDA Autoscaling

[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-keda-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-keda-ibm-acc-gpu-vllm-x.yaml)

## Overview

This guide combines [Fast Model Actuation (FMA)](../fast-model-actuation/README.md) with **[saturation-based autoscaling via KEDA](../workload-autoscaling/keda-epp-saturation/README.md)**. A KEDA `ScaledObject` scales the FMA server-requesting Deployment (`fma-requester`) on Endpoint Picker (EPP) flow-control metrics; each new requesting pod drives the FMA controllers to bring a vLLM instance online via a **hot or warm start**. (See the [FMA guide](../fast-model-actuation/README.md#overview) for what hot and warm start mean.)

### How the autoscaling works

- The **EPP** (with the `flowControl` feature gate enabled) emits `llm_d_epp_flow_control_pool_saturation` — a 0.0–1.0+ measure of how saturated the inference pool is — and `llm_d_epp_request_running`.
- The **KEDA ScaledObject** queries these from Prometheus and scales `fma-requester` out when saturation crosses the threshold and in when it subsides.
- Each **scale-up** replica reserves a GPU, which the FMA controller turns into a hot or warm start; each **scale-down** deletes a requesting pod, and the controller puts its vLLM to sleep.

The KEDA objects here reuse the `ScaledObject` and `TriggerAuthentication` from the [keda-epp-saturation](../workload-autoscaling/keda-epp-saturation/README.md) guide directly. The  `ScaledObject` targets the GPU-reserving `fma-requester` Deployment (not a vLLM server Deployment), caps `maxReplicaCount` at the launcher pool's capacity, and tunes the HPA `behavior` for prompt hot-wakes and gentle scale-down.

## Configuration

| Parameter                | Value                                                        |
| ------------------------ | ------------------------------------------------------------ |
| Model                    | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B)      |
| Requester replicas       | 1 (KEDA floor) … 2 (KEDA ceiling)                            |
| Launcher count           | 1 (per matching GPU node)                                    |
| GPUs per requester pod   | 1                                                            |
| Scale metric (primary)   | `llm_d_epp_flow_control_pool_saturation` (threshold `0.7`)   |
| Scale metric (secondary) | `llm_d_epp_request_running` (threshold `16`)                 |
| Autoscaler               | KEDA `ScaledObject` → HPA on `fma-requester`                 |
| Router                   | llm-d-router-standalone (EPP `flowControl` enabled)          |

## Prerequisites

This guide assumes you have a Kubernetes cluster with GPU nodes, the [llm-d router](../../guides/recipes/router/README.md) infrastructure, and a **Prometheus/monitoring stack that scrapes the EPP** (KEDA reads the saturation metric from it). If you are starting from an existing llm-d deployment, the Gateway API Inference Extension CRDs may already be installed and you can skip that step.

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- **Monitoring stack with Prometheus over HTTPS** — See [autoscaling prerequisites](../workload-autoscaling/README.md#prerequisites) and [Prometheus Setup Guide](../../docs/operations/observability/setup.md). This includes [KEDA installation](../workload-autoscaling/README.md#kubernetes-metrics-adapter).

- **EPP flow control enabled** — The `llm_d_epp_flow_control_pool_saturation` metric KEDA scales on requires the EPP flow control feature gate. This guide's router values enable the `flowControl` gate for you (applied in [step 4](#4-deploy-the-llm-d-router-epp-flow-control-enabled)), so no extra action is needed. See [EPP Flow Control](../../docs/architecture/core/router/epp/flow-control.md) for details on flow control behavior.

- **Fast Model Actuation** — This guide reuses the base [Fast Model Actuation guide](../fast-model-actuation/README.md)'s manifests (`LauncherConfig`, `LauncherPopulationPolicy`, the requester Deployment) as a kustomize base and stands up its own FMA controllers. You do not need to deploy it separately.

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
export MODEL=Qwen/Qwen3-32B
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

The FMA controllers need cluster-level access to list nodes (for the launcher-populator) and namespace-level access for launcher pods to read their own pod spec:

<!-- guide:deploy.rbac start -->
```bash
# ClusterRole (cluster-scoped): the FMA controllers list nodes for the launcher-populator.
# Reused verbatim from the base fast-model-actuation guide (identical RBAC).
kubectl apply -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/clusterrole.yaml

# ServiceAccount + Role (namespaced): the launcher pods run as the
# fma-launcher ServiceAccount so the state-change-reflector sidecar can
# patch its own pod (the dual-pods.llm-d.ai/vllm-instance-signature
# annotation). Reused verbatim from the base fast-model-actuation guide.
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/role.yaml

# RoleBinding (namespaced): bind the Role to the fma-launcher ServiceAccount.
# Created imperatively (not from a static manifest) so ${NAMESPACE} drives both the
# binding's namespace and the subject ServiceAccount's namespace.
kubectl create rolebinding fma-launcher-pod-state-writer \
  --role=fma-launcher-pod-state-writer \
  --serviceaccount=${NAMESPACE}:fma-launcher \
  -n ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding (cluster-scoped): grant the EPP's ServiceAccount
# system:auth-delegator.
kubectl create clusterrolebinding "${GUIDE_NAME}-epp-auth-delegator-${NAMESPACE}" \
  --clusterrole=system:auth-delegator \
  --serviceaccount=${NAMESPACE}:${GUIDE_NAME}-epp \
  --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding (cluster-scoped): grant the EPP metrics-reader
# ServiceAccount `get` on the EPP's /metrics nonResourceURL, so the token
# user-workload-monitoring uses to scrape the EPP is authorized.
kubectl create clusterrolebinding "${GUIDE_NAME}-epp-metrics-reader-${NAMESPACE}" \
  --clusterrole=inference-gateway-metrics-reader \
  --serviceaccount=${NAMESPACE}:fast-model-actuation-keda-epp-metrics-reader \
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

The router values for this guide enable the EPP `flowControl` feature gate so the pool-saturation metric is emitted for KEDA to scale on:

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 5. Deploy the Model Server (Dual Pods)

Apply the FMA custom resources — `InferenceServerConfig`, `LauncherConfig`, and `LauncherPopulationPolicy` — **together with** the server-requesting `fma-requester` Deployment **and the KEDA autoscaling layer** in a single `kubectl apply -k modelserver/`. This guide's `modelserver/` reuses the base [`fast-model-actuation`](../fast-model-actuation) guide's manifests as a kustomize base — so the shared FMA plumbing (`LauncherConfig`, `LauncherPopulationPolicy`, the requester Deployment) is maintained once — and patches only the KEDA-specific deltas: the `InferenceServerConfig` serves Qwen3-32B in dev mode, and the requester starts at 1 replica (the KEDA floor). The autoscaler is pulled in via `../keda/overlays/ocp`. Bundling KEDA here — rather than as a separate apply — is what lets the benchmark's kustomize standup bring up the autoscaler; the overlay is namespace-agnostic, so all of these resources land in `${NAMESPACE}` from the `-n` flag:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/

kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=300s
```
<!-- guide:deploy.modelserver end -->

> [!NOTE]
> This guide uses [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B), which is publicly accessible and does not require a HuggingFace token. Saturation is a ratio of offered load to serving capacity, so any model saturates given enough load — the choice of a 32B model here is practical, not definitional: at the request rate this guide's workload generates on a single 80 GB GPU, a small model's service rate is high enough that `pool_saturation` stays near zero and never crosses the `0.7` threshold, so only the secondary `llm_d_epp_request_running` trigger would ever fire. A 32B model's lower service rate and larger KV footprint push saturation past the threshold under the same load, so the primary saturation trigger — the feature this guide exists to demonstrate — actually does the scaling.

> [!NOTE]
> `launcherCount` is **per matching node**. Setting `launcherCount: 1` creates one launcher pod on each node labeled `nvidia.com/gpu.present: "true"`. Only launchers bound to a requesting pod actually start a vLLM instance.

### 6. Enable Saturation Autoscaling (KEDA)

On OpenShift the KEDA layer (the `ScaledObject`, its `TriggerAuthentication`, the
EPP `ServiceMonitor`, and the two metrics-reader ServiceAccounts + tokens) was
**already deployed in step 5** — the `ocp` overlay is a resource of
`modelserver/`, so a single `kubectl apply -k modelserver/` brought it up with
the model server. The overlay points the triggers at thanos-querier and enables
bearer auth; the service-ca operator injects the Thanos CA (`service-ca.crt`)
into the token Secret automatically, so **no `prometheus-token` copy is
required** on OpenShift. The three cluster-scoped ClusterRoleBindings the layer
needs (`system:auth-delegator`, `inference-gateway-metrics-reader`, and
`cluster-monitoring-view`) were created imperatively in the [RBAC step](#2-grant-rbac-permissions),
where `${NAMESPACE}` drives the subject namespace (the namespace-agnostic overlay
cannot carry them). This step therefore only confirms the `ScaledObject`
reconciled `Ready`:

<!-- guide:deploy.keda start -->
```bash
kubectl wait --for=condition=Ready --timeout=120s \
  scaledobject/fast-model-actuation-keda-saturation -n ${NAMESPACE}
```
<!-- guide:deploy.keda end -->

You should see:
- The `fma-requester` Deployment at its floor (`minReplicaCount`), reconciled by a KEDA-created HPA
- A `ScaledObject/fast-model-actuation-keda-saturation` reporting `Ready=True`
- Launcher pods `Running` (one per GPU node); FMA controller and Router/EPP pods `Running`

> [!NOTE]
> **Generic Kubernetes (non-OpenShift).** The steps above target OpenShift's
> Thanos/user-workload-monitoring. On generic Kubernetes with the bundled llm-d
> Prometheus stack, remove `../keda/overlays/ocp` from
> `modelserver/kustomization.yaml`, then apply the base KEDA bundle separately
> and first copy the Prometheus token into the workload namespace so the base
> `TriggerAuthentication` can authenticate. The `cluster-monitoring-view` binding
> and the auto-provisioned metrics-reader tokens are OpenShift-specific and are
> not needed here.

<!-- llm-d-cicd:skip start -->
```bash
# Generic Kubernetes only (do NOT run on OpenShift):
kubectl get secret prometheus-token -n ${MONITORING_NAMESPACE} -o yaml \
  | sed "s/namespace: ${MONITORING_NAMESPACE}/namespace: ${NAMESPACE}/" \
  | kubectl apply -n ${NAMESPACE} -f -
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/keda/base
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

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request end -->

### 3. Demonstrate Warm Start

This demonstrates a [**warm start**](../fast-model-actuation/README.md#overview) — a new vLLM instance created on an existing launcher. With launchers up but no sleeping instance yet, scaling the requester triggers warm starts. KEDA is paused first so the HPA does not immediately reconcile the manual scale (it is resumed at the end of the hot-start demo below). Look for `create_instance` in the dual-pods-controller log:

<!-- guide:verify.tests.warm_start start -->
```bash
kubectl annotate scaledobject fast-model-actuation-keda-saturation -n ${NAMESPACE} autoscaling.keda.sh/paused="true" --overwrite

kubectl scale deployment fma-requester -n ${NAMESPACE} --replicas=2

time kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=300s

kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=dual-pods-controller --tail=500 | grep -i "create_instance" || true
```
<!-- guide:verify.tests.warm_start end -->

### 4. Demonstrate Hot Start

This demonstrates a [**hot start**](../fast-model-actuation/README.md#overview) — waking a *sleeping* vLLM instance, which resumes in seconds. Scaling to `0` puts the instances to sleep; scaling back up wakes them. Look for `wake` in the dual-pods-controller log. The last command resumes KEDA autoscaling:

<!-- guide:verify.tests.hot_start start -->
```bash
kubectl annotate scaledobject fast-model-actuation-keda-saturation -n ${NAMESPACE} autoscaling.keda.sh/paused="true" --overwrite

set -eu
IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
kubectl scale deployment fma-requester -n ${NAMESPACE} --replicas=0
kubectl wait --for=delete pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s
# Confirm the launcher actually went to sleep before we wake it.
kubectl wait pod -l app.kubernetes.io/component=launcher -n ${NAMESPACE} \
  --for='jsonpath={.metadata.labels.dual-pods\.llm-d\.ai/sleeping}=true' \
  --timeout=120s
kubectl scale deployment fma-requester -n ${NAMESPACE} --replicas=2
time kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=120s
kubectl get pods -l app.kubernetes.io/component=launcher -n ${NAMESPACE} \
  -o 'jsonpath={range .items[*]}{.metadata.name}{" sleeping="}{.metadata.labels.dual-pods\.llm-d\.ai/sleeping}{"\n"}{end}' || true
# Post-wake inference request THROUGH THE EPP.
kubectl run curl-postwake --rm -i --restart=Never --attach \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --pod-running-timeout=180s \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'set -e; resp=$(curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\", \"max_tokens\": 5}"); echo "${resp}"; echo "${resp}" | jq -e ".choices[0].text | length > 0" >/dev/null'

kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=dual-pods-controller --tail=500 | grep -i "wake" || true

kubectl annotate scaledobject fast-model-actuation-keda-saturation -n ${NAMESPACE} autoscaling.keda.sh/paused- || true
```
<!-- guide:verify.tests.hot_start end -->

Re-run the inference request from step 2 to confirm the model is serving again.

> [!NOTE]
> These demos pause KEDA so the manual scaling is not immediately reconciled by the HPA. In steady-state operation you do **not** scale `fma-requester` by hand — KEDA drives it from the saturation metric, and the same hot/warm start paths shown here fire automatically as load rises and falls. The benchmark below exercises exactly that.

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking. It defaults to the `nop` harness (which stands the stack up and validates it end-to-end without driving synthetic load); the richer, FMA/KEDA-specific experimentation workflow lives in [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) itself.

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
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/keda/overlays/ocp --ignore-not-found=true

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/ --ignore-not-found=true

kubectl wait --for=delete pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s

kubectl wait --for=delete pod -l app.kubernetes.io/component=launcher -n ${NAMESPACE} --timeout=120s

helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/fast-model-actuation/rbac/role.yaml --ignore-not-found=true

kubectl delete rolebinding fma-launcher-pod-state-writer -n ${NAMESPACE} --ignore-not-found=true

kubectl delete clusterrolebinding "${GUIDE_NAME}-epp-auth-delegator-${NAMESPACE}" --ignore-not-found=true

kubectl delete clusterrolebinding "${GUIDE_NAME}-epp-metrics-reader-${NAMESPACE}" --ignore-not-found=true

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
