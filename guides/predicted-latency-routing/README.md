# Predicted Latency-Based Routing

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-ibm-acc-gpu-vllm-x.yaml)
[![E2E (AMD ROCm)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-amd-ci-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-amd-ci-acc-rocm-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-intel-acc-xpu-vllm-x.yaml)

## Overview

Static token-count and queue-depth heuristics break down on **long-generation workloads**—such as agentic tool loops, long-horizon coding, and reasoning-heavy decode—where output lengths vary by orders of magnitude across requests. A replica decoding a single multi-thousand-token trace looks lightly loaded by active request count while stalling incoming prefill behind decode memory bandwidth and KV-cache contention.

Where the [Optimized Baseline](../optimized-baseline/README.md) gates cache affinity with a static `peakPrefillThroughput` heuristic (which must be calibrated offline per hardware/model and ignores decode TPOT), this path pairs the llm-d Router (`epp`) with online-trained `xgboost` regression sidecars that predict per-request **TTFT** and **TPOT** on every candidate server from live server state and route on those predictions, optionally enforcing per-request latency SLOs.

Skip this path when the inference pool is heterogeneous (mixed accelerators, model variants, or serving configurations): the predictor's feature vector captures pod load state without encoding hardware shape, so it assumes a homogeneous pool.

The reference deployment reuses the Optimized Baseline model servers (on NVIDIA and AMD GPU, two `Qwen/Qwen3-32B` replicas with tensor parallelism 2 and a RoPE-scaled 131,072-token context) and deploys a 4-container router pod (`epp`, `envoy-proxy`, `training-server`, and `prediction-server-1`). Two replicas is the smallest pool in which the router has a routing choice to make.

For how the predictor is trained and queried on the request path, and the features it uses, see [Latency Predictor](../../docs/architecture/router/latency-predictor.md).

### Scheduling modes

Two router configurations ship with this guide, selected with `SLO_AWARE`:

| `SLO_AWARE` | Values file | Scheduling pipeline | Trained targets | Behavior & wire contract |
| --- | --- | --- | --- | --- |
| `false` (default) | [`router/predicted-latency.values.yaml`](router/predicted-latency.values.yaml) | `loose-affinity-filter` → `latency-scorer` → `weighted-random-picker` | `ttft` (end-to-end latency) | Routing only; no SLO headers required. Sets `streamingMode: false`, so it works with both streaming and non-streaming clients. |
| `true` | [`router/predicted-latency-slo.values.yaml`](router/predicted-latency-slo.values.yaml) | `latency-slo-admitter` + `strict-affinity-filter` → `slo-headroom-tier-filter` → `loose-affinity-filter` → `latency-scorer` → `weighted-random-picker` | `ttft + tpot` | Enforces `x-llm-d-slo-ttft-ms` and/or `x-llm-d-slo-tpot-ms`: sheddable requests (priority < 0) that no server can meet are rejected at admission, while priority >= 0 requests fall back to the server with the smallest predicted SLO deficit. Sets `streamingMode: true`, so **every request must be sent with `"stream": true`**. |

The [Latency Predictor](../../docs/architecture/router/latency-predictor.md#scheduling-strategy) page describes the plugins behind each mode and when to use each [streaming mode](../../docs/architecture/router/latency-predictor.md#streaming-mode).

### Key parameters

| Parameter | Component | Default in this guide | Role |
| --- | --- | --- | --- |
| `streamingMode` | `predicted-latency-producer` (`epp`) | `false` (`true` when `SLO_AWARE=true`) | When `false`, trains `ttft` on end-to-end request latency; when `true`, measures first-chunk TTFT and inter-token TPOT on streamed responses (`"stream": true`). |
| `maxPrefixTokensToMatch` | `approx-prefix-cache-producer` (`epp`) | `262144` (`131072` in P/D & multimodal values) | Maximum prompt prefix tokens hashed to compute `prefix_cache_score`. Sized for long-context agentic prompts; lower to match shorter max prompt lengths and reduce EPP CPU/memory. |
| `LATENCY_RETRAINING_INTERVAL_SEC` | `training-server` (`:8000`) | `10` | Interval in seconds between background `xgboost` retraining cycles. |
| `LATENCY_MIN_SAMPLES_FOR_RETRAIN_FRESH` | `training-server` (`:8000`) | `10` (predictor default) | Training samples a model needs before it is retrained when the pod started with no saved model, which is every new pod (`/models` is an `emptyDir`). It overrides the chart's `LATENCY_MIN_SAMPLES_FOR_RETRAIN=100`, which applies only when saved models are loaded at startup, such as after a `training-server` container restart. |
| `LATENCY_TEST_TRAIN_RATIO` | `training-server` (`:8000`) | `0.1` | Fraction of incoming samples (`10%`, selected by a hash of each sample) held out as a test set; the other `90%` enter the stratified training buckets (capped at `500` samples each). |

### Composing with other paths

The predictor runs entirely in the router pod, so it layers onto other model server topologies unchanged. Two more values files ship with this guide for that purpose; they are not part of this guide's deployment. To use one, deploy the other guide's model servers and install the router with the file in place of `ROUTER_VALUES`:

- [`router/predicted-latency-pd.values.yaml`](router/predicted-latency-pd.values.yaml) for the [P/D disaggregation](../pd-disaggregation/README.md) pods (`llm-d.ai/guide=pd-disaggregation`).
- [`router/predicted-latency-multimodal.values.yaml`](router/predicted-latency-multimodal.values.yaml) for the [multimodal serving](../multimodal-serving/README.md) aggregated pool (`llm-d.ai/guide=aggregation`).

How the scoring changes in each is described in [Composing with other topologies](../../docs/architecture/router/latency-predictor.md#composing-with-other-topologies).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves exactly one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | 🟡 community | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · vLLM runs a RoPE-scaled 131,072-token context · `INFRA_PROVIDER`: `base`, `gke` |
| AMD GPU | `amd` | `Qwen/Qwen3-32B` | ✅ validated | — | Instinct MI355X · 2 replicas × TP=2 (4 GPUs) · RoPE-scaled 131,072-token context |
| Intel XPU | `xpu` | `Qwen/Qwen3-0.6B` | ✅ validated | — | 2 replicas × 1 GPU via DRA · single-GPU pods have no room for the long-context `Qwen3-32B` config |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | 🟡 community | — | GKE only · 2 replicas × 8 chips (`2x4`, TP=8) · `INFRA_PROVIDER`: `base`, `gke` |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3-32B` | 🟡 community | — | GKE only · 2 replicas × 4 chips (`2x2x1`, TP=8) · `INFRA_PROVIDER`: `base`, `gke` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

The latency predictor is engine-agnostic: it reads the same server state the router already collects. The SGLang and TPU (`tpu/v6`, `tpu/v7`) overlays reuse the Optimized Baseline model servers unchanged (40,960-token native context, without the 131,072-token RoPE patch).

> [!NOTE]
> On OpenShift, the latency predictor sidecars may require additional OpenShift-specific runtime adjustments beyond the manifests in this guide.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total). The router pod runs four containers (`epp`, `envoy-proxy`, `training-server`, and `prediction-server-1`) requesting a combined **18 vCPUs and 24 GiB of memory** (8 vCPUs and 16 GiB for the router and Envoy, plus 10 vCPUs and 8 GiB for the latency predictor sidecars), so your untainted CPU node pool must have nodes with at least 32 vCPUs (e.g. `n2-standard-32`).

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Set up the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

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
export GUIDE_NAME=predicted-latency-routing
export NAMESPACE=llm-d-predicted-latency
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export SLO_AWARE=false # options: false, true
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v6, tpu/v7
export MODEL_SERVER=vllm # options: vllm, sglang
export INFRA_PROVIDER=base # options: base, gke
export MODEL=Qwen/Qwen3-32B # set to the model your accelerator serves (table above); Intel XPU serves Qwen/Qwen3-0.6B
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

**Create a target namespace for the installation:**

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below). Select a [scheduling mode](#scheduling-modes):

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

<!-- guide:deploy.router_values.routing start -->
```bash
# only when SLO_AWARE=false:
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/predicted-latency.values.yaml"
```
<!-- guide:deploy.router_values.routing end -->

</details>
<details>
<summary><b>SLO-aware</b></summary>

<!-- guide:deploy.router_values.slo-aware start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when SLO_AWARE=true:
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/predicted-latency-slo.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.router_values.slo-aware end -->

</details>
<!-- tabs:end -->

Both values files enable the latency predictor sidecars (`router.latencyPredictor.enabled: true`) and select model server pods labeled `llm-d.ai/guide=optimized-baseline`, the label of the Optimized Baseline model servers this guide reuses.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining `MONITORING_VALUES` (see [Collect Metrics](../../docs/operations/observability/metrics.md)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:deploy.monitoring_values.standard start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values.standard end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

<!-- guide:deploy.monitoring_values.gke start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true and INFRA_PROVIDER=gke:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values.gke end -->

</details>
<!-- tabs:end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/router/proxy.md), with an Envoy sidecar in front of the router and the latency predictor sidecars (one training server, one prediction server) next to it. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your accelerator and model server (`INFRA_PROVIDER=gke` applies only to NVIDIA GPU and TPU; use `base` elsewhere). Every overlay reuses the [Optimized Baseline](../optimized-baseline/README.md#2-deploy-the-model-server) model server; the NVIDIA and AMD GPU vLLM overlays add a RoPE-scaled 131,072-token context so long prompts and long generations fit:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

**(Optional) Deploy the monitoring resources for model servers** (see [Collect Metrics](../../docs/operations/observability/metrics.md)):

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

No manual `PodMonitor` deployment is required when [GKE automatic application monitoring](../../docs/operations/observability/metrics.md#step-1-scrape-model-server-metrics) is enabled; GKE automatically provisions `PodMonitoring` resources for the model servers.

</details>
<!-- tabs:end -->

Wait for the router (`${GUIDE_NAME}-epp-*`, `4/4 Running`) and both model server pods (`optimized-baseline-*`, labeled `llm-d.ai/guide=optimized-baseline`, `1/1 Running`) to become `Ready` (`kubectl get pods -n ${NAMESPACE}`) before starting [Verification](#verification).

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster:**

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

<!-- guide:verify.tests.request.routing start -->
```bash
# only when SLO_AWARE=false:
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request.routing end -->

Expected output (a non-streaming JSON completion):

```text
{"id":"cmpl-1d8b1f40-c104-4169-9881-5a0ec858d14e","object":"text_completion","model":"Qwen/Qwen3-32B","choices":[{"index":0,"text":" I hope you're well. I have a question about a math problem. The","finish_reason":"length"}],...}
```

</details>
<details>
<summary><b>SLO-aware</b></summary>

<!-- guide:verify.tests.request.slo-aware start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when SLO_AWARE=true:
# streamingMode: true measures TTFT and TPOT on streamed responses only
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -N -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\", \"stream\": true}"'
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.request.slo-aware end -->

Expected output (server-sent event stream chunks):

```text
data: {"id":"cmpl-87bab755-e81a-4611-b7ae-99b4dc452e28","object":"text_completion","model":"Qwen/Qwen3-32B","choices":[{"index":0,"text":" I","finish_reason":null}]}

data: {"id":"cmpl-87bab755-e81a-4611-b7ae-99b4dc452e28","object":"text_completion","model":"Qwen/Qwen3-32B","choices":[{"index":0,"text":" hope","finish_reason":null}]}
...
data: [DONE]
```

</details>
<!-- tabs:end -->

### 3. Verify predicted-latency routing

A served request only proves the stack is up: if the predictor is unreachable, the router silently falls back to heuristic scoring and requests still succeed. To confirm the mechanism is engaged, send enough traffic for the predictor to learn from, then check that the router fed it training samples and that the prediction server loaded a trained model.

**Send 200 requests with varying output lengths:**

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

<!-- guide:verify.tests.traffic.routing start -->
```bash
# only when SLO_AWARE=false:
# 200 requests, 10 at a time, with output lengths from 16 to 128 tokens:
# every completed request becomes a training sample for the predictor
kubectl run traffic-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'for i in $(seq 1 200); do
      curl -sS -o /dev/null -w "%{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"Request ${i}: explain how an LLM router picks a model server.\", \"max_tokens\": $((16 * (i % 8 + 1)))}" &
      [ $((i % 10)) -eq 0 ] && wait
    done | sort | uniq -c'
```
<!-- guide:verify.tests.traffic.routing end -->

</details>
<details>
<summary><b>SLO-aware</b></summary>

<!-- guide:verify.tests.traffic.slo-aware start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when SLO_AWARE=true:
# 200 streamed requests, 10 at a time, with output lengths from 16 to
# 128 tokens: streaming lets the router measure TPOT, so every
# completed request trains both the TTFT and TPOT models
kubectl run traffic-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'for i in $(seq 1 200); do
      curl -sS -N -o /dev/null -w "%{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"Request ${i}: explain how an LLM router picks a model server.\", \"max_tokens\": $((16 * (i % 8 + 1))), \"stream\": true}" &
      [ $((i % 10)) -eq 0 ] && wait
    done | sort | uniq -c'
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.traffic.slo-aware end -->

</details>
<!-- tabs:end -->

Expected output (all 200 requests return HTTP 200):

```text
    200 200
```

**Read the latency predictor's state** from the training and prediction server sidecars in the router pod:

<!-- guide:verify.tests.predictor start -->
```bash
# The latency predictor sidecars run in the router (EPP) pod: the training
# server on port 8000 and the prediction server on port 8001. Read them
# through the Kubernetes API server pod proxy (no port-forward needed)
EPP_POD=$(kubectl get pods -n ${NAMESPACE} -o name | grep "^pod/${GUIDE_NAME}-epp-" | head -1 | cut -d/ -f2)
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${EPP_POD}:8000/proxy/metrics" \
  | awk '/^training_samples_count\{model="ttft"/ {ttft += $NF} /^training_samples_count\{model="tpot"/ {tpot += $NF} END {printf "training samples: ttft=%d tpot=%d\n", ttft, tpot}'
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${EPP_POD}:8001/proxy/status"; echo
```
<!-- guide:verify.tests.predictor end -->

What to expect: after 200 requests, `training_samples_count` reports roughly `175–190` samples rather than `200` because `LATENCY_TEST_TRAIN_RATIO=0.1` holds out about 10% of samples as a test set (each training bucket is also capped, so the count stops growing on long runs). The prediction server reports `"is_ready": true` from startup (see the placeholder-model note in [Observability & Troubleshooting](#observability--troubleshooting)); a `last_model_load` that advances after the traffic shows it loaded a model trained on these samples:

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

Routing only trains `ttft`; `tpot` stays at `0`:

```text
training samples: ttft=188 tpot=0
{"is_ready":true,"model_type":"xgboost","objective_type":"mean","quantile":0.9,"last_model_load":"2026-10-13T18:27:40.348701Z","training_server_url":"http://localhost:8000","models_exist":{"ttft_model":true,"tpot_model":true,"ttft_gated":false,"tpot_gated":false},"ensemble_active":false}
```

</details>
<details>
<summary><b>SLO-aware</b></summary>

Streamed responses train both `ttft` and `tpot`:

```text
training samples: ttft=177 tpot=181
{"is_ready":true,"model_type":"xgboost","objective_type":"mean","quantile":0.9,"last_model_load":"2026-10-13T18:30:58.085058Z","training_server_url":"http://localhost:8000","models_exist":{"ttft_model":true,"tpot_model":true,"ttft_gated":false,"tpot_gated":false},"ensemble_active":false}
```

</details>
<!-- tabs:end -->

**With `MONITORING=true`, compare the router's predicted and actual TTFT/TPOT histograms:**

<!-- tabs:start group=monitoring -->
<details open>
<summary><b>OSS Prometheus / OpenShift</b></summary>

<!-- guide:verify.tests.router_metrics.standard start -->
```bash
# only when MONITORING=true:
# Actual vs. predicted TTFT/TPOT histograms of the router, read through the
# API server service proxy
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_request_(predicted_)?(ttft|tpot)_seconds_count' || true
```
<!-- guide:verify.tests.router_metrics.standard end -->

</details>
<details>
<summary><b>GKE (Managed Prometheus)</b></summary>

<!-- guide:verify.tests.router_metrics.gke start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true and INFRA_PROVIDER=gke:
# Actual vs. predicted TTFT/TPOT histograms of the router, authenticated
# with the GMP metrics-reader secret
TOKEN=$(kubectl get secret ${GUIDE_NAME}-metrics-reader-secret -n ${NAMESPACE} -o jsonpath='{.data.token}' | base64 -d)
kubectl run metrics-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="GUIDE_NAME=${GUIDE_NAME}" \
  --env="TOKEN=${TOKEN}" \
  -- /bin/sh -c 'curl -sS -H "Authorization: Bearer ${TOKEN}" "http://${GUIDE_NAME}-epp:9090/metrics"' \
  | grep -E '^llm_d_epp_request_(predicted_)?(ttft|tpot)_seconds_count' || true
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.router_metrics.gke end -->

</details>
<!-- tabs:end -->

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

Expected output:

```text
llm_d_epp_request_predicted_ttft_seconds_count{model_name="",plugin_name="predicted-latency-producer",plugin_type="predicted-latency-producer",target_model_name="Qwen/Qwen3-32B"} 201
llm_d_epp_request_ttft_seconds_count{fairness_id="default-flow",model_name="Qwen/Qwen3-32B",priority="0",streaming="false",target_model_name="Qwen/Qwen3-32B"} 201
```

`llm_d_epp_request_predicted_ttft_seconds_count` is above zero and grows with `llm_d_epp_request_ttft_seconds_count`: the router recorded a prediction for every request it routed.

</details>
<details>
<summary><b>SLO-aware</b></summary>

Expected output (adds `predicted_tpot_seconds_count`):

```text
llm_d_epp_request_predicted_tpot_seconds_count{model_name="",plugin_name="predicted-latency-producer",plugin_type="predicted-latency-producer",target_model_name="Qwen/Qwen3-32B"} 201
llm_d_epp_request_predicted_ttft_seconds_count{model_name="",plugin_name="predicted-latency-producer",plugin_type="predicted-latency-producer",target_model_name="Qwen/Qwen3-32B"} 201
llm_d_epp_request_ttft_seconds_count{fairness_id="default-flow",model_name="Qwen/Qwen3-32B",priority="0",streaming="true",target_model_name="Qwen/Qwen3-32B"} 201
```

`llm_d_epp_request_predicted_ttft_seconds_count` and `llm_d_epp_request_predicted_tpot_seconds_count` are above zero and grow with `llm_d_epp_request_ttft_seconds_count`: the router recorded a prediction for every request it routed.

</details>
<!-- tabs:end -->

If the predicted counts stay at zero while the actual count grows, the router fell back to heuristic scoring; see [Observability & Troubleshooting](#observability--troubleshooting).

### 4. (Optional) Send a request with SLOs

<!-- tabs:start group=slo -->
<details open>
<summary><b>Routing only (Default)</b></summary>

The routing-only pipeline has no `latency-slo-admitter` or `slo-headroom-tier-filter`, so it does not enforce `x-llm-d-slo-*` headers. Select **SLO-aware** to send a request with SLOs.

</details>
<details>
<summary><b>SLO-aware</b></summary>

**Send a streamed request with TTFT and TPOT SLOs:**

<!-- guide:verify.tests.slo_request start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when SLO_AWARE=true:
# A streamed request with TTFT and TPOT SLOs: the SLO-aware values set
# streamingMode: true, so requests must be sent with "stream": true
kubectl run slo-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -N -o /dev/null -w "HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
    -H "Content-Type: application/json" \
    -H "x-llm-d-slo-ttft-ms: 2000" \
    -H "x-llm-d-slo-tpot-ms: 100" \
    -d "{\"model\": \"${MODEL}\", \"prompt\": \"Explain the difference between prefill and decode.\", \"max_tokens\": 128, \"stream\": true}"'
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.slo_request end -->

Expected output:

```text
HTTP 200
```

The request returns `HTTP 200` and is placed on a server predicted to meet both SLOs (`slo-headroom-tier-filter` + `latency-scorer`). When no candidate server is predicted to meet the requested SLOs, `latency-slo-admitter` checks the request's priority—set via an `InferenceObjective` CRD referenced by the `x-llm-d-inference-objective` header (or its legacy alias `x-gateway-inference-objective`; default priority is `0` when omitted):

- **Sheddable traffic (`priority < 0`):** Rejected at admission before consuming GPU capacity. The admitter fails open, admitting the request anyway, when no predictions are available or when some server is idle or has a nearly empty KV cache.
- **Standard and high-priority traffic (`priority >= 0`):** Admitted on a best-effort basis and routed to the candidate server with the smallest predicted SLO deficit (most headroom).

</details>
<!-- tabs:end -->

> [!IMPORTANT]
> **Trust Boundary:** `x-llm-d-slo-ttft-ms`, `x-llm-d-slo-tpot-ms`, and `x-llm-d-inference-objective` (plus its legacy alias `x-gateway-inference-objective`) are client-supplied HTTP headers. In production, untrusted clients must not self-assert latency SLOs or traffic priority: sanitize or strip incoming `x-llm-d-*`, `x-slo-*`, and `x-gateway-inference-objective` headers at your authenticated ingress gateway (or Envoy `ext_authz` filter) and inject authoritative SLO and objective headers based on the caller's authenticated tenant tier (see [EPP HTTP Headers Reference](../../docs/api-reference/epp-http-headers.md)).

Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## Observability & Troubleshooting

With monitoring enabled, three router metrics tell you whether predicted-latency routing is healthy under **sustained load in production or benchmark scenarios** (note that the small request burst in [Verification](#verification) only warms up the predictor and does not reflect steady-state convergence or SLO behavior; see the [architecture doc](../../docs/architecture/router/latency-predictor.md#observability) for the full metric reference):

| Signal | What healthy looks like |
| --- | --- |
| `llm_d_epp_request_predicted_ttft_seconds` and `llm_d_epp_request_ttft_prediction_duration_seconds` | Non-zero samples: the router is calling the prediction server. If they stay empty, the predictor is not being called; check the router logs for `predicted-latency-producer` errors. |
| `llm_d_epp_request_predicted_ttft_seconds` vs. `llm_d_epp_request_ttft_seconds` | Predictions track reality: over a rolling window, the two converge after warm-up. |
| `llm_d_epp_request_slo_violation_total` | With achievable SLOs and a matching `streamingMode`, stays near zero under normal load and increments only under genuine saturation (if it climbs at low QPS, see the symptom table below). |

> [!NOTE]
> **Placeholder model on cold start:** At startup, `training-server` has no saved model, so it fits a placeholder `xgboost` model on a single synthetic row that predicts a near-constant ~10 ms. `prediction-server-1` downloads it at startup and reports `"is_ready": true` before any traffic arrives, but its predictions carry no information. Once a model has at least `10` training samples (`LATENCY_MIN_SAMPLES_FOR_RETRAIN_FRESH`), the next 10-second retraining cycle replaces the placeholder, and `last_model_load` advances on the prediction server's next 30-second sync.

| Symptom | Likely cause |
| --- | --- |
| Prediction metrics empty | Prediction server unreachable: the router falls back to a composite heuristic score. Check sidecar readiness and `PREDICTION_SERVER_URL`. |
| `"is_ready": true` before any traffic, or constant predictions during cold start | `prediction-server-1` is serving the placeholder model. Send traffic until `training_samples_count` reaches `10` per model, then allow up to `40s` (one retraining cycle plus one sync) for `last_model_load` to advance. |
| Large, persistent drift between predicted and actual TTFT | `streamingMode` does not match the traffic (e.g. `false` on a streaming workload when you want true TTFT), or the workload drifted outside the training window. |
| High TPOT SLO violation rate at low QPS | `streamingMode: false`: TPOT is not being trained. Use the SLO-aware values file, which sets it to `true`, and stream requests. |
| Prediction-based routing degrades to baseline | Predictor error or sidecar restart: an expected fallback, not a failure. Investigate the sidecar logs. |

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->

## Related

- [Latency Predictor Architecture](../../docs/architecture/router/latency-predictor.md): plugin pipeline, ML model, scaling characteristics, metric reference.
- [llm-d/llm-d-router](https://github.com/llm-d/llm-d-router): source for the EPP plugins and per-plugin configuration references.
- [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor): source for the training and prediction server Python code.
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms): design rationale and benchmark results.
