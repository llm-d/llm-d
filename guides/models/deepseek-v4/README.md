# DeepSeek-V4-Pro on GB200

## Overview

This guide deploys [DeepSeek-V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) on NVIDIA
GB200 NVL72 with vLLM prefill/decode disaggregation (NIXL KV transfer) in a wide
expert-parallel pattern, managed as a single `DisaggregatedSet` (a pair of LeaderWorkerSets).
Wide-EP spans multiple nodes over GB200's cross-node NVLink (MNNVL) fabric, provisioned through
an NVIDIA DRA `ComputeDomain`.

It composes the [wide expert parallelism](../../wide-ep/README.md)
and [P/D disaggregation](../../pd-disaggregation/README.md) foundations
with P/D-aware, prefix-cache-aware routing, and ships a range of prefill : decode operating
points, from a 16-GPU low-latency layout up to a 56-GPU high-throughput layout.

These manifests were tested on Oracle Cloud Infrastructure (OCI, `BM.GPU.GB200.4` nodes). Storage
and DRA may need to be adapted to your environment.

### Default Configuration

| Parameter          | Value                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------- |
| Model              | [deepseek-ai/DeepSeek-V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)      |
| Accelerator        | NVIDIA GB200 NVL72 (4 GPUs per node), cross-node NVLink via `ComputeDomain`            |
| Serving topology   | P/D disaggregated `DisaggregatedSet`; prefill DEP8, decode TP=8, DEP8 or DEP16 (see [Deployments](#2-deploy-the-model-server)) |
| DP model           | Hybrid load balancing (`--data-parallel-hybrid-lb`), expert parallelism enabled        |
| MoE backend        | `deep_gemm_mega_moe`                                                                   |
| KV transfer        | NixlConnector                                                                          |
| KV cache           | FP8, block size 256                                                                    |
| Max model length   | 9280 tokens                                                                            |
| Model storage      | Node-local NVMe (`hostPath` `/mnt/numa0/hf-cache`, ~850 GB model)                      |

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| NVIDIA GB200 NVL72 | `gpu` | `deepseek-ai/DeepSeek-V4-Pro` | 🟡 community | OCI `BM.GPU.GB200.4` (4 GPUs per node) · 4 to 14 nodes (16 to 56 GPUs) by `DEPLOYMENT` · P/D disaggregated wide-EP; needs the NVIDIA DRA driver (`ComputeDomain`) |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- Deploy the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1`
  or newer. When installing with Helm, pass `--set enableDisaggregatedSet=true` to enable the
  `DisaggregatedSet` validating webhook and RBAC used by the model server.

- Install the [NVIDIA DRA driver for GPUs](https://github.com/NVIDIA/k8s-dra-driver-gpu). The
  cross-node NVLink fabric is provisioned through a
  [`ComputeDomain`](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-cds.html#computedomains-multi-node-nvlink-simplified)
  resource. The guide ships its own `ComputeDomain` CR
  ([`providers/oci/compute-domain.yaml`](modelserver/gpu/vllm/providers/oci/compute-domain.yaml));
  applying a deployment creates it and the workers claim a channel from it, so no manual fabric
  setup is needed.

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Install the [monitoring stack](../../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d): the manifests, Helm values, and Kustomize overlays it applies live next to this guide. Set the branch and clone the repo (if you already have a checkout, skip this and run the remaining commands from inside it):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

**Set the guide-specific environment variables** (`DEPLOYMENT` picks one of the [deployments](#2-deploy-the-model-server)):

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=deepseek-v4
export NAMESPACE=llm-d-deepseek-v4
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu
export MODEL_SERVER=vllm # options: vllm
export DEPLOYMENT=oci-low-latency # options: oci-low-latency, oci-low-latency-scaled, oci-mid-curve, oci-balanced, oci-high-tpt, oci-high-tpt-dep16, oci-max-tpt, oci-ultra-tpt, oci-3p2d-dep8-dep16-flashinfer
export MODEL=deepseek-ai/DeepSeek-V4-Pro # the model every deployment serves
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

**Install the Gateway API Inference Extension CRDs:**

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

**Check that the `ComputeDomain` CRD is present** (if this returns `NotFound`, install the NVIDIA DRA driver before continuing):

<!-- guide:prerequisites.compute_domain start -->
```bash
# The NVIDIA DRA driver must be installed: the deployments claim a ComputeDomain
kubectl get crd computedomains.resource.nvidia.com
```
<!-- guide:prerequisites.compute_domain end -->

**Create a target namespace for the installation:**

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

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

**Prepare the paths to the `helm` values files** for the `llm-d` router. The router values
([`deepseek-v4.values.yaml`](router/deepseek-v4.values.yaml)) run separate `prefill` and
`decode` scheduling profiles (prefix-cache, queue and active-request scoring for prefill;
active-request scoring for decode) over all 8 DP rank ports, and pin each request to one
`DisaggregatedSet` revision during rollouts:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

Each deployment is a different prefill : decode operating point. Set `DEPLOYMENT` to one of them
(default `oci-low-latency`, the smallest):

| Deployment | Layout | Nodes / GPUs |
|---|---|---|
| `oci-low-latency` | 1 prefill (DEP8) : 1 decode (TP=8) | 4 / 16 |
| `oci-low-latency-scaled` | 1 prefill (DEP8) : 4 decode (TP=8) | 10 / 40 |
| `oci-mid-curve` | 1 prefill : 1 decode (DEP8 each) | 4 / 16 |
| `oci-balanced` | 2 prefill (DEP8) : 1 decode (DEP16) | 8 / 32 |
| `oci-high-tpt` | 2 prefill : 1 decode (DEP8 each) | 6 / 24 |
| `oci-high-tpt-dep16` | 3 prefill (DEP8) : 1 decode (DEP16) | 10 / 40 |
| `oci-max-tpt` | 3 prefill : 1 decode (DEP8 each) | 8 / 32 |
| `oci-ultra-tpt` | 4 prefill (DEP8) : 1 decode (DEP16) | 12 / 48 |
| `oci-3p2d-dep8-dep16-flashinfer` | 3 prefill (DEP8) : 2 decode (DEP16), flashinfer | 14 / 56 |

**Apply the Kustomize overlay** for your deployment. The deployments compose the `providers/oci`
overlay (node-local NVMe model cache and the `ComputeDomain` claim) with a topology component
from `modelserver/gpu/vllm/components/`:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}
```
<!-- guide:deploy.modelserver end -->

Wait for the pods to become ready (the ~850 GB model takes a while to download and load):

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=DeepSeek-V4-Pro -w
```

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)). With DP-aware scheduling, each DP rank is available at `podip:port`, where each port is `rank0`-`rank7`; this overlay scrapes each rank's port:

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/monitoring
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

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

**Check that the router reaches a model server that serves `MODEL`:** the response must name the model and carry generated tokens:

<!-- guide:verify.tests.served_model start -->
```bash
# The router must reach a model server that serves MODEL and returns tokens
kubectl run serve-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'R=$(curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Say hello.\", \"max_tokens\": 16}")
    echo "${R}" | grep -q "\"model\":\"${MODEL}\"" && echo "${R}" | grep -q "\"choices\"" \
      && echo "OK: ${MODEL} served through the router" \
      || { echo "FAIL: ${R}"; exit 1; }'
```
<!-- guide:verify.tests.served_model end -->

## Benchmarking

Benchmark profiles and results for this guide are not yet published.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
