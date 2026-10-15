<!--
    Prose is free-form; scripts/guide.py only touches the bash code
    blocks between paired HTML-comment markers naming YAML paths.
-->

# <Guide Title> <!-- markdownlint-disable-line MD033 -->

## Overview

<!-- 1–2 paragraphs: what capability or model topology this guide deploys, and when to use it. -->

## Supported Accelerators and Model Servers

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `<HuggingFace/Model-Name>` | 🟡 community | 🟡 community | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` |
| Intel XPU | `xpu` | `<HuggingFace/Model-Name>` | 🟡 community | ❌ [not supported](https://github.com/llm-d/llm-d/issues/2665) | Data Center GPU Max 1550+ · 2 replicas × 1 GPU via DRA |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.
- (Optional) Install the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

Set the guide-specific environment variables (replace `HF_TOKEN_PLACEHOLDER` with a real HuggingFace token):

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=<guide-name>
export NAMESPACE=llm-d-<guide-name>
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, xpu
export MODEL_SERVER=vllm # options: vllm, sglang
export INFRA_PROVIDER=base # options: base, gke
export MODEL=<HuggingFace/Model-Name> # set to the model your accelerator serves (table above)
```
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

Create the `llm-d-hf-token` secret in your target namespace:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

## Installation Instructions

### 1. Deploy the llm-d Router

Prepare the paths to the `helm` values files for the `llm-d` router:

<!-- guide:deploy.router_values start -->
```bash
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

(Optional) Enable Prometheus monitoring on the `llm-d` router:

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

Deploy the router in [Standalone Mode](../../docs/architecture/router/proxy.md). To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router):

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

Apply the Kustomize overlay for your accelerator and model server:

<!-- guide:deploy.modelserver start -->
<!-- variants:start -->
<details open data-when="MODEL_SERVER=vllm">
<summary><b>vLLM</b></summary>

```bash
# vLLM model server overlay
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/vllm/${INFRA_PROVIDER}/
```

</details>
<details data-when="MODEL_SERVER=sglang">
<summary><b>SGLang</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
# SGLang model server overlay
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/sglang/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:deploy.modelserver end -->

(Optional) Deploy the monitoring resources for model servers:

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Proxy

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

### 3. Verify the Mechanism

Confirm that the capability deployed by this guide is active:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

Inspect the vLLM metrics through the Kubernetes API server proxy:

<!-- guide:verify.tests.mechanism[0] start -->
```bash
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^vllm:request_success_total' || true
done
```
<!-- guide:verify.tests.mechanism[0] end -->

</details>
<details>
<summary><b>SGLang</b></summary>

Inspect the SGLang metrics through the Kubernetes API server proxy:

<!-- guide:verify.tests.mechanism[1] start -->
<!-- llm-d-cicd:skip start -->
```bash
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^sglang:num_requests_total' || true
done
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.mechanism[1] end -->

</details>
<!-- tabs:end -->

## Cleanup

<!-- guide:cleanup start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->
