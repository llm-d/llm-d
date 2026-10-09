# Predicted Latency-Based Routing

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-ibm-acc-gpu-vllm-x.yaml)
[![E2E (AMD ROCm)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-amd-ci-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-amd-ci-acc-rocm-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-predicted-latency-routing-intel-acc-xpu-vllm-x.yaml)

## Overview

This guide routes each inference request to the model server predicted to serve it fastest. It is the well-lit path for **long-generation workloads**, such as agentic and long-horizon coding or reasoning-heavy decode, where output lengths vary by orders of magnitude between requests and the number of queued or running requests says little about how long a server will take to serve the next one.

The [Optimized Baseline](../optimized-baseline/README.md) scores servers with heuristics: prefix-cache affinity and token load, combined with fixed weights. Predicted latency-based routing replaces the heuristic score with a learned one. For every candidate server, the llm-d Router asks an online-trained XGBoost model for the request's **time to first token (TTFT)** and **time per output token (TPOT)** on that server, given its current state (KV-cache utilization, queue depth, running requests, prefix-cache match), and routes on those predictions.

Every completed request becomes a training sample, so the model tracks the live workload rather than a calibration done ahead of time. Optionally, clients attach per-request TTFT/TPOT SLOs and the router only places a request on a server predicted to meet them.

The reference deployment reuses the Optimized Baseline model servers (on NVIDIA GPU, two `Qwen/Qwen3-32B` replicas with tensor parallelism 2 and a RoPE-scaled 131,072-token context) and deploys the router with the latency predictor sidecars enabled. Two replicas is the smallest pool in which the router has a choice to make. For how the component works internally (the plugin pipeline, the ML model, scaling characteristics, the full metric list), see the [Latency Predictor architecture](../../docs/architecture/advanced/latency-predictor.md).

### Architecture

<p align="center">
  <picture>
    <img src="../../docs/assets/latency-predictor.svg" alt="Latency Predictor">
  </picture>
</p>

The router (EPP) pod runs two latency predictor sidecars next to the scheduler:

- **Training server**: trains the XGBoost TTFT and TPOT models on the latencies of completed requests and periodically publishes them.
- **Prediction server**: loads the latest models and predicts each request's TTFT and TPOT on every candidate server from that server's current state.

During the request flow:

1. A request arrives at the proxy, which forwards it to the router.
2. The router queries the prediction server for every candidate server.
3. The `latency-scorer` (and, with `SLO_AWARE=true`, the `slo-headroom-tier-filter`) picks the server from the predictions.
4. The proxy forwards the request to that model server, which processes it and returns the response.
5. The router sends the observed latencies to the training server, which adds them to its training set for the next model update.

### When to use this path

Pick it when:

- Your workload has **high variance in prompt and completion length**: long generations next to short ones, so queue depth alone is a poor proxy for true load.
- Your clients can express **per-request latency SLOs** (interactive vs. batch) and you want the router to enforce them.
- Static weight tuning between cache affinity and load has become **fragile** as traffic shifts.

Skip it when your pool is **heterogeneous**: mixed accelerator types, model variants, or serving configurations in the same pool produce inaccurate predictions, because the predictor assumes a single pod shape.

### Scheduling modes

Two router configurations ship with this guide, selected with `SLO_AWARE`:

| `SLO_AWARE` | Values file | Behavior |
| --- | --- | --- |
| `false` (default) | [`router/predicted-latency.values.yaml`](router/predicted-latency.values.yaml) | Routing only: a loose prefix-cache affinity filter gated on predicted TTFT, then the `latency-scorer` and a weighted-random pick. The predictor trains on end-to-end request latency (`streamingMode: false`), so it works for streaming and non-streaming clients. No request headers are needed. |
| `true` | [`router/predicted-latency-slo.values.yaml`](router/predicted-latency-slo.values.yaml) | SLO-aware: requests carry `x-llm-d-slo-ttft-ms` and/or `x-llm-d-slo-tpot-ms`, and the `slo-headroom-tier-filter` prefers servers predicted to meet them. Sheddable requests (priority < 0) are rejected at admission when no server can meet the SLO, rather than routed to a guaranteed miss. Enforcing a TPOT SLO means predicting TPOT, which is trained from the inter-token gaps of streamed responses, so this file sets `streamingMode: true` and **every request must be sent with `"stream": true`**. |

### Composing with other paths

The predictor runs entirely in the router pod, so it composes with other model server topologies without changing them. Two more values files ship with this guide for that purpose; they are not part of this guide's deployment:

- [`router/predicted-latency-pd.values.yaml`](router/predicted-latency-pd.values.yaml) layers predicted latency on the [P/D disaggregation](../pd-disaggregation/README.md) pipeline: the prefill profile is scored purely on predicted TTFT and the decode profile purely on predicted TPOT, and prefix-cache affinity runs on prefill only. It targets pods labeled `llm-d.ai/guide=pd-disaggregation` and sets `streamingMode: true`. To use it, deploy the P/D guide's model servers and install the router with this file in place of `ROUTER_VALUES`.
- [`router/predicted-latency-multimodal.values.yaml`](router/predicted-latency-multimodal.values.yaml) does the same for the [multimodal serving](../multimodal-serving/README.md) aggregated pool. It keeps the multimodal `token-producer` (image to token-count estimation), lowers the affinity threshold to fit multimodal cacheable fractions, and trains on end-to-end latency. It targets pods labeled `llm-d.ai/guide=multimodal-aggregation`.

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

The latency predictor is engine-agnostic: it reads the same server state the router already collects. The SGLang overlay is the Optimized Baseline's NVIDIA GPU SGLang model server, without the long-context patch.

> [!NOTE]
> On OpenShift, the latency predictor sidecars may require additional OpenShift-specific runtime adjustments beyond the manifests in this guide.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total). The router pod also runs the latency predictor sidecars, which request about 10 CPU cores and 8 GiB of memory on top of the router itself.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Install the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below). With `SLO_AWARE=true`, the second command switches to the SLO-aware values (see [Scheduling modes](#scheduling-modes)):

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/predicted-latency.values.yaml"
```
<!-- llm-d-cicd:skip start -->
```bash
# only when SLO_AWARE=true:
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/predicted-latency-slo.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.router_values end -->

Both values files enable the latency predictor sidecars (`router.latencyPredictor.enabled: true`) and select model server pods labeled `llm-d.ai/guide=optimized-baseline`, the label of the Optimized Baseline model servers this guide reuses.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)). It also disables authentication on the router's `/metrics` endpoint, which the [router metrics check](#3-verify-predicted-latency-routing) relies on:

<!-- guide:deploy.monitoring_values start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router and the latency predictor sidecars (one training server, one prediction server) next to it. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### 3. Observability & Troubleshooting

With monitoring enabled, three router metrics tell you whether predicted-latency routing is healthy (see the [architecture doc](../../docs/architecture/advanced/latency-predictor.md#observability) for the full metric reference):

| Signal | What healthy looks like |
| --- | --- |
| `llm_d_epp_request_predicted_ttft_seconds` and `llm_d_epp_request_ttft_prediction_duration_seconds` | Non-zero samples: the router is calling the prediction server. If they stay empty, the predictor is not being called; check the router logs for `predicted-latency-producer` errors. |
| `llm_d_epp_request_predicted_ttft_seconds` vs. `llm_d_epp_request_ttft_seconds` | Predictions track reality: over a rolling window, the two converge after warm-up. |
| `llm_d_epp_request_slo_violation_total` | With SLO-annotated traffic, increments only under genuine saturation. |

| Symptom | Likely cause |
| --- | --- |
| Prediction metrics empty | Prediction server unreachable: the router falls back to a composite heuristic score. Check sidecar readiness and `PREDICTION_SERVER_URL`. |
| Large, persistent drift between predicted and actual TTFT | `streamingMode` does not match the traffic (e.g. `false` on a streaming workload when you want true TTFT), or the workload drifted outside the training window. |
| High TPOT SLO violation rate at low QPS | `streamingMode: false`: TPOT is not being trained. Use the SLO-aware values file, which sets it to `true`, and stream requests. |
| Prediction-based routing degrades to baseline | Predictor error or sidecar restart: an expected fallback, not a failure. Investigate the sidecar logs. |

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster** (`MODEL` must be the model your accelerator serves, see the **Served model** column in [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)):

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

### 3. Verify predicted-latency routing

A served request only proves the stack is up: if the predictor is unreachable, the router silently falls back to heuristic scoring and requests still succeed. To confirm the mechanism is engaged, send enough traffic for the predictor to learn from, then check that the router fed it training samples and that the prediction server loaded a trained model.

**Send 200 requests with varying output lengths:**

<!-- guide:verify.tests.traffic start -->
```bash
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
<!-- guide:verify.tests.traffic end -->

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

What to expect: all 200 requests return HTTP 200. The training server reports `ttft` samples roughly equal to the number of requests sent so far (each bucket is capped, so the count stops growing on long runs). With the routing-only values, `tpot` stays at `0`: TPOT is only trained from streamed responses (`SLO_AWARE=true`). The prediction server's status reports `"is_ready": true` once it has loaded a model trained on these samples: the training server retrains every 10 seconds once it holds at least 100 samples, so re-run the check after a few seconds if it is still `false`.

**With `MONITORING=true`, compare the router's predicted and actual TTFT histograms:**

<!-- guide:verify.tests.router_metrics start -->
```bash
# only when MONITORING=true:
# Actual vs. predicted TTFT histograms of the router, read through the
# API server service proxy
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_request_(predicted_)?ttft_seconds_count' || true
```
<!-- guide:verify.tests.router_metrics end -->

`llm_d_epp_request_predicted_ttft_seconds_count` is above zero and grows with `llm_d_epp_request_ttft_seconds_count`: the router recorded a prediction for the requests it routed. If it stays at zero while the actual count grows, the router fell back to heuristic scoring; see [Observability & Troubleshooting](#3-observability--troubleshooting).

### 4. (Optional) Send a request with SLOs

**With `SLO_AWARE=true`, send a streamed request with TTFT and TPOT SLOs:**

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

The request returns HTTP 200 and is placed on a server predicted to meet both SLOs, if one exists. Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

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

- [Latency Predictor Architecture](../../docs/architecture/advanced/latency-predictor.md): plugin pipeline, ML model, scaling characteristics, metric reference.
- [llm-d/llm-d-router](https://github.com/llm-d/llm-d-router): source for the EPP plugins and per-plugin configuration references.
- [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor): source for the training and prediction server Python code.
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms): design rationale and benchmark results.
