# Kimi-K3.1-Pro on TPU 7x (SGLang)

This recipe targets long-context, multi-turn serving: a 480K context with prefix-aware routing and
a host-DRAM prefix cache, so repeated conversation prefixes are reused instead of recomputed.

## Overview

This guide deploys [moonshotai/Kimi-K3.1-Pro](https://huggingface.co/moonshotai/Kimi-K3.1-Pro) on
Google TPU 7x with SGLang on Torch TPU and the llm-d router. It composes the
[tiered prefix cache](../../tiered-prefix-cache/README.md) foundation with prefix-aware routing:

- **Multi-host serving**: Kimi-K3.1-Pro (1.56 TB, MXFP4 experts) runs as one TP=32 × EP=8 engine per
  `tpu7x` `2x2x4` slice (4 hosts × 4 chips), as a LeaderWorkerSet of 4 pods. Rank 0 serves the API;
  only leader pods are router endpoints.
- **Tiered prefix cache (KV cache offloading)** to host DRAM: SGLang HiCache over TPU Sync keeps
  evicted prefixes (the sparse-MLA / DSA pages and the KDA recurrent-state slots) in a host pool twice
  the size of the HBM pool, and loads them back when a multi-turn conversation reuses the
  prefix instead of recomputing its prefill.
- **Prefix-aware routing**: the router keeps an index of what each replica holds in HBM and host DRAM
  and routes each turn of a conversation to the replica that holds its cache, balanced against
  in-flight token load.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [moonshotai/Kimi-K3.1-Pro](https://huggingface.co/moonshotai/Kimi-K3.1-Pro) (MXFP4 experts, 1.56 TB; 69 KDA + 24 sparse-MLA layers with a DSA indexer) |
| Replicas | 2 LeaderWorkerSet groups (4 pods each) |
| Accelerator | Google TPU 7x (`tpu7x`) |
| Topology | `2x2x4` (4 hosts × 4 chips, 32 TensorCores) per replica; 32 chips for the default 2 replicas |
| Parallelism | TP=32 × EP=8 (SGLang's Kimi-K3.1 TPU recipe) |
| Context length | 491,520 tokens (480K). The batch-64 decode kernel's SMEM caps the context at about 496K tokens; the HBM KV pool holds 532,352 tokens |
| KV cache | fp8, `--page-size 128`, radix cache; HiCache host tier with `--hicache-ratio 2` (1,064,832 host tokens per rank) |
| Model weights | `ReadOnlyMany` Hyperdisk ML volume hydrated once from a GCS copy of the Hugging Face repo |
| Model server | SGLang on Torch TPU (`docker.io/lmsysorg/sglang:dev-kimi-k31-pro-tpu`, pinned by digest) |

> [!NOTE]
> Serving is text only, and OpenAI tool calling does not work in this configuration yet. This SGLang
> TPU recipe runs with `--grammar-backend none`, so requests that carry an OpenAI `tools` array are
> rejected with HTTP 400, and coding-agent clients that send tools will fail. The `kimi_k3`
> reasoning parser works. The server keeps `--tool-call-parser=kimi_k3` so tool calling works
> without a manifest change once SGLang on TPU supports it; until then it has no effect.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | SGLang | Notes |
| --- | --- | --- | --- | --- |
| Google TPU v7x | `tpu` | `moonshotai/Kimi-K3.1-Pro` | 🟡 community | GKE only · SGLang on Torch TPU · 2 replicas, each a LeaderWorkerSet of 4 pods on one `tpu7x` `2x2x4` slice (16 chips): TP=32 × EP=8, MXFP4 experts, sparse-MLA (DSA) attention, fp8 KV cache, 480K context, host-DRAM HiCache over TPU Sync (2× the HBM pool) · text only (`--grammar-backend=none` in this SGLang recipe; OpenAI `tools` requests are not yet enabled) · weights on a ReadOnlyMany Hyperdisk ML volume hydrated from GCS |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- A GKE cluster with two TPU 7x (`tpu7x`) `2x2x4` node pools (4 nodes of 4 chips each), with
  [Workload Identity Federation for GKE](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity)
  enabled. Each node needs about 800 GiB of allocatable host memory (weight loading peaks at about
  660 GiB per pod, plus the HiCache host pools).

- The [LeaderWorkerSet (LWS) controller](../../../docs/infrastructure/model-server-orchestration.md).

- A Google Cloud Storage bucket in the cluster's region, exported as `MODEL_BUCKET`, and a
  Hyperdisk ML quota of at least 2 TB in the TPU zone.

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

**Set the guide-specific environment variables:**

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=kimi-k3-1
export NAMESPACE=llm-d-kimi-k3-1
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=tpu # options: tpu
export MODEL_SERVER=sglang # options: sglang
export MODEL=moonshotai/Kimi-K3.1-Pro # the served model name
export MODEL_BUCKET=my-model-bucket # GCS bucket that holds the moonshotai/Kimi-K3.1-Pro copy
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

**Install the LeaderWorkerSet controller** (skip if your cluster already runs LWS):

<!-- guide:prerequisites.lws start -->
<!-- llm-d-cicd:skip start -->
```bash
# helm upgrade never updates CRDs, so apply the chart's CRDs first (see LWS
# "Upgrade by Helm"); this upgrades them on clusters that run an older LWS.
helm pull oci://registry.k8s.io/lws/charts/lws --version=${LWS_VERSION} --untar
kubectl apply --server-side --force-conflicts -f lws/crds
helm upgrade --install lws oci://registry.k8s.io/lws/charts/lws \
  --version=${LWS_VERSION} \
  --namespace lws-system --create-namespace \
  --wait --timeout 300s
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.lws end -->

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

### Stage the weights

At 1.56 TB the checkpoint is far larger than a TPU node's boot disk, and reading it over a network
filesystem dominates start-up. Copy it once into `MODEL_BUCKET` (in the cluster's region):

<!-- guide:prerequisites.model_bucket start -->
<!-- llm-d-cicd:skip start -->
```bash
# One-time copy of the 1.56 TB checkpoint into a bucket in the cluster's region. The
# download needs about 1.6 TB of free local disk; run it on a GCE VM in the same region
# with a large enough disk if your workstation does not have it.
hf download moonshotai/Kimi-K3.1-Pro --local-dir ./Kimi-K3.1-Pro
gcloud storage cp -r ./Kimi-K3.1-Pro gs://${MODEL_BUCKET}/moonshotai/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.model_bucket end -->

Then create the model volume: a `ReadOnlyMany`
[Hyperdisk ML](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/hyperdisk-ml)
PVC that the [GKE Volume Populator](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/volume-populator)
hydrates from the bucket once. Every model-server pod mounts it read-only at `/models`. Give the
populator's transfer job read access to the bucket:

<!-- guide:prerequisites.model_storage_access start -->
<!-- llm-d-cicd:skip start -->
```bash
# The GKE Volume Populator's transfer job reads the bucket as this Kubernetes ServiceAccount.
kubectl create serviceaccount kimi-k3-1-model-populator -n ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
export PROJECT_ID=$(gcloud config get-value project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding gs://${MODEL_BUCKET} \
  --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/kimi-k3-1-model-populator" \
  --role=roles/storage.objectViewer
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.model_storage_access end -->

**Create the StorageClass, data source and PVC.** The PVC hydrates when the first pod that mounts it is scheduled:

<!-- guide:prerequisites.model_storage start -->
<!-- llm-d-cicd:skip start -->
```bash
# A ReadOnlyMany Hyperdisk ML volume that every model-server pod mounts at /models,
# hydrated once from gs://${MODEL_BUCKET}/moonshotai by the GKE Volume Populator.
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: kimi-k3-1-hyperdisk-ml
provisioner: pd.csi.storage.gke.io
parameters:
  type: hyperdisk-ml
  provisioned-throughput-on-create: "6400Mi"
allowVolumeExpansion: false
volumeBindingMode: WaitForFirstConsumer
---
apiVersion: datalayer.gke.io/v1
kind: GCPDataSource
metadata:
  name: kimi-k3-1-models
spec:
  cloudStorage:
    serviceAccountName: kimi-k3-1-model-populator
    uri: gs://${MODEL_BUCKET}/moonshotai/
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: kimi-k3-1-models
spec:
  accessModes: [ReadOnlyMany]
  storageClassName: kimi-k3-1-hyperdisk-ml
  resources:
    requests:
      storage: 2000Gi
  dataSourceRef:
    apiGroup: datalayer.gke.io
    kind: GCPDataSource
    name: kimi-k3-1-models
EOF
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.model_storage end -->

## Installation Instructions

### 1. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router:

<!-- guide:deploy.router_values start -->
```bash
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

> [!NOTE]
> The router's block size (`blockSizeTokens: 128`) equals SGLang's `--page-size`, and
> `maxPrefixTokensToMatch` equals `--context-length`. `lruCapacityPerServer` is sized for the HBM
> pool plus the HiCache host pool of one TP rank, from the engine's startup log, and
> `peakPrefillThroughput` was measured with the
> [calibration tool](../../recipes/router/calibration/README.md); re-measure both if you change the
> context length, `--hicache-ratio` or the hardware.

**Deploy the router** in [Standalone Mode](../../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router. Keep the release name `${GUIDE_NAME}`: the verification steps look up the `${GUIDE_NAME}-epp` Service by that name. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

**Apply the Kustomize overlay:**

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/
```
<!-- guide:deploy.modelserver end -->

Wait for the model server to become ready. A cold start loads the weights (about 5–7 minutes from
a local high-throughput volume) and compiles every prefill and decode graph (about 30 minutes);
budget about 45 minutes:

```bash
kubectl wait --for=condition=Available leaderworkerset/kimi-k3-1-tpu-sglang-decode -n ${NAMESPACE} --timeout=7200s
```

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

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

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Explain how a simple agent loop works in 3 sentences.\"}"'
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

The first request to each replica takes about two minutes while the compiled graphs are loaded;
later requests return in about a second.

## Benchmarking

This guide comes with an `inference-perf` benchmark preset (defined in [inference-perf.yaml](inference-perf.yaml)):
multi-turn coding-style sessions over a shared system prompt, a large per-session repository context,
and simulated pauses between turns (`tool_call_latency_sec`, client-side think time only; the requests
carry no `tools` array). The configuration parameters include:

| Workload Characteristic | Metric / Distribution Type | Min | Max | Mean / Constant | Std Dev | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shared System Prompt** | Constant | - | - | 3,000 tokens | - | Common base instructions, libraries, and API schemas shared across all sessions. Highly cacheable. |
| **Dynamic System Prompt** | Lognormal | 32,000 | 420,000 | 220,000 tokens | 160,000 | Repository context, file indexes, and session-specific code context, sized so sessions reach ~460K tokens and active KV spills into the host-DRAM HiCache tier. |
| **Turns per Conversation** | Lognormal | 20 | 200 | 50 turns | 30 | Depth of the multi-turn conversational loop, avoiding premature Turn-0 resets during 20-turn benchmark stages. |
| **Input Tokens per Turn** | Lognormal | 100 | 10,000 | 1,500 tokens | 1,200 | Incremental prompt extensions (test logs, follow-ups, modified code blocks) on each turn. |
| **Output Tokens per Turn** | Lognormal | 50 | 4,096 | 425 tokens | 825 | Model generations per turn. |
| **Tool Call Latency** | Lognormal | 1s | 100s | 15 seconds | 55 | Simulated client-side delay between turns (compilation, unit tests, search). |

```bash
# CONCURRENCY_LEVEL is the number of concurrent coding sessions (at 16, active KV fills the
# 1.60M-token/replica HBM + HiCache tier); NUM_REQUESTS is 20 per session; SEED is varied per
# concurrency level so prompts don't overlap across runs.
export CONCURRENCY_LEVEL=16
export NUM_REQUESTS=$((20 * CONCURRENCY_LEVEL))
export SEED=$((7 + CONCURRENCY_LEVEL))

kubectl delete job inference-perf -n ${NAMESPACE} --ignore-not-found
envsubst < ${REPO_ROOT}/guides/models/${GUIDE_NAME}/inference-perf.yaml | kubectl apply -n ${NAMESPACE} -f -
kubectl logs -n ${NAMESPACE} job/inference-perf -f
```

## Benchmark Results

Results for `moonshotai/Kimi-K3.1-Pro` are being collected with the workload above on two `tpu7x`
`2x2x4` replicas and will be published here with the image digest and benchmark parameters of each
run.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
# The Hyperdisk ML volume holds a copy of the weights; delete it when you no longer need it.
kubectl delete pvc kimi-k3-1-models -n ${NAMESPACE} --ignore-not-found=true
kubectl delete gcpdatasource kimi-k3-1-models -n ${NAMESPACE} --ignore-not-found=true
kubectl delete serviceaccount kimi-k3-1-model-populator -n ${NAMESPACE} --ignore-not-found=true
kubectl delete storageclass kimi-k3-1-hyperdisk-ml --ignore-not-found=true
export PROJECT_ID=$(gcloud config get-value project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
gcloud storage buckets remove-iam-policy-binding gs://${MODEL_BUCKET} \
  --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/kimi-k3-1-model-populator" \
  --role=roles/storage.objectViewer

kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
