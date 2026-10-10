# Optimized Baseline

[![E2E (AMD ROCM)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-amd-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-amd-acc-rocm-vllm-x.yaml)
[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-ibm-acc-gpu-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-intel-acc-xpu-vllm-x.yaml)

## Overview

Traditional HTTP requests are fast, uniform, and cheap. Standard round-robin request scheduling strategies balance this load well.

LLM requests break all three assumptions. They are:

* **Multi-turn** - conversations and agentic tool loops send the same growing prefix repeatedly
* **Slow** - a single request can take over a minute generating tokens
* **Non-uniform** - range from 1000s of reasoning tokens to a 100k+ context tokens

The llm-d Router injects awareness of the LLM-workload into the load-balancing layer considering **prefix-cache affinity** and **server load metrics**.

This guide deploys the recommended out of the box [configuration](https://github.com/llm-d/llm-d-router/blob/main/docs/architecture.md) for most vLLM and SGLang deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing.

The optimized-baseline defaults to two main routing criteria:

- **Prefix-cache aware** using the [prefix cache affinity filter](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/filter/prefixcacheaffinity/README.md), which narrows candidates to "sticky" endpoints with high estimated prompt prefix cache reuse, with a saturation-aware override that spreads load when endpoints get hot.

- **Load-aware** using the [token load scorer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/tokenload/README.md), which scores endpoints based on the total prefill token load handled by each model server.

Both plugins are used with their built-in defaults — no per-deployment tuning is required for this guide's reference setup (Qwen3-32B on H100 80&nbsp;GB, TP=2). If you deploy a **different model or accelerator**, the saturation-aware override gate keys off the `peakPrefillThroughput` of the filter, which is hardware- and model-specific; measure your own with the shared [calibration recipe](../recipes/router/calibration/README.md) and set it on the filter.

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves exactly one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | TensorRT-LLM | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | ✅ validated | 🟡 community | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` |
| AMD GPU | `amd` | `Qwen/Qwen3-32B` | ✅ validated | 🟡 community | — | Instinct MI355X · 2 replicas × TP=2 (4 GPUs) |
| Intel XPU | `xpu` | `Qwen/Qwen3-0.6B` | ✅ validated | — | — | Data Center GPU Max 1550+ · 2 replicas × 1 GPU via DRA · fp16 |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | ✅ validated | — | — | GKE only · 2 replicas × 8 chips (`2x4`, TP=8) |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3-32B` | 🟡 community | — | — | GKE only · 2 replicas × 4 chips (`2x2x1`, TP=8) |
| Google TPU v7 (dynamic slicing) | `tpu/v7-dynamic-slice` | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | 🟡 community | — | — | GKE dynamic slicing + Kueue · one sub-slice per replica (`TPU_SLICE_TOPOLOGY`: `2x2x1`, `2x2x2`); see [below](#2-deploy-the-model-server) |
| Rebellions NPU | `npu` | `openai/gpt-oss-120b` | 🟡 community | — | — | RBLN-CR series · 2 replicas × 1 NPU via DRA |
| CPU | `cpu` | `meta-llama/Llama-3.2-3B-Instruct` | 🟡 community | — | — | x86 with AMX or AVX512-BF16 (Sapphire Rapids+, GCP C3, AMD Zen 4+) · 2 replicas × 64 cores / 64 GiB (CPUs without AMX/AVX512-BF16, e.g. Cascade/Ice Lake, need `--dtype=float32` for the bf16 model) |
| Iluvatar GPU | `iluvatar` | `deepseek-ai/DeepSeek-V4-Flash` | 🟡 community | — | — | BI-V150 (dual-die) · 1 replica × 4 GPUs |
| MetaX GPU | `metax` | `deepseek-ai/DeepSeek-R1-Distill-Llama-70B` | 🟡 community | — | — | 2 replicas × TP=8 (16 GPUs) |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total). If your cluster has fewer resources, adjust `replicas` and `--tensor-parallel-size` in the [model server patch](./modelserver/gpu/vllm/base/patch-vllm.yaml) for your environment.

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
export GUIDE_NAME=optimized-baseline
export NAMESPACE=llm-d-optimized-baseline
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v6, tpu/v7, tpu/v7-dynamic-slice, npu, cpu, iluvatar, metax
export MODEL_SERVER=vllm # options: vllm, sglang, trtllm
export INFRA_PROVIDER=base # options: base, gke, amd-ci
export TPU_SLICE_TOPOLOGY=2x2x1 # options: 2x2x1, 2x2x2
export MODEL=Qwen/Qwen3-32B # set to the Served model for your accelerator (table above)
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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment commands below):

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
```
<!-- variants:start -->
<details open data-when="MODEL_SERVER=vllm,sglang">
<summary><b>vLLM / SGLang</b></summary>

```bash
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```

</details>
<details data-when="MODEL_SERVER=trtllm">
<summary><b>TensorRT-LLM</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}-trtllm.values.yaml"
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:deploy.router_values end -->

> [!NOTE]
> The `prefix-cache-affinity-filter` in [`router/optimized-baseline.values.yaml`](router/optimized-baseline.values.yaml) defaults to `peakPrefillThroughput` tuned for the reference setup (`Qwen3-32B` on H100 80&nbsp;GB, TP=2). On a different model or accelerator, miscalibrated `peakPrefillThroughput` can prevent the saturation override from spreading load across pods — measure and set your value with the [calibration guide](../recipes/router/calibration/README.md) (or check the [configuration matrix](../recipes/router/calibration/configuration-matrix.md) for pre-measured values).

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in **one** of two modes: Standalone Mode (the default, used by every other guide) or Gateway Mode, which fronts the `InferencePool` with a Kubernetes Gateway–managed proxy. The Verification section below has matching steps for each mode:

<!-- tabs:start group=mode -->
<details open>
<summary><b>Standalone Mode</b></summary>

This deploys the llm-d Router in [Standalone Mode](../../docs/architecture/core/router/proxy.md) with an Envoy sidecar (default):

<!-- guide:deploy.standalone start -->
```bash
# Assuming base-directory is the root of the llm-d repo
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

To use **agentgateway** as the sidecar proxy instead of Envoy, see [router recipes](../recipes/router/README.md).

</details>
<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. _Deploy a Kubernetes Gateway_ named by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. _Deploy the llm-d router and an HTTPRoute_ that connects it to the Gateway as follows:

<!-- guide:deploy.gateway start -->
```bash
export GW_PROVIDER_NAME=none # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${GW_PROVIDER_NAME} \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.gateway end -->

</details>
<!-- tabs:end -->

### 2. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

The NVIDIA GPU, AMD, CPU, Intel XPU and TPU v6/v7 vLLM overlays drain in-flight requests during rollouts and scale-down: `--shutdown-timeout=45`, plus a 15s `preStop` sleep and a 75s termination grace period from the [`graceful-shutdown` component](../recipes/modelserver/components/graceful-shutdown/kustomization.yaml). The Iluvatar, MetaX, Rebellions NPU and TPU v7 dynamic-slice overlays do not drain yet. To tune these, see [Graceful Shutdown & Request Draining](../../docs/operations/lifecycle/graceful-shutdown.md).

**Apply the Kustomize overlays** for your specific backend (`INFRA_PROVIDER=gke` applies only to accelerators available on GKE: NVIDIA GPU, TPU, and CPU; use `base` elsewhere):

<!-- tabs:start group=modelserver -->
<details open>
<summary><b>Default</b></summary>

<!-- guide:deploy.modelserver.standard start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.standard end -->

</details>
<details data-when="ACCELERATOR_TYPE=tpu/v7-dynamic-slice">
<summary><b>Google TPU v7 (dynamic slicing)</b></summary>

<!-- guide:deploy.modelserver.dynamic_slice start -->
<!-- llm-d-cicd:skip start -->
```bash
# One sub-slice of shape TPU_SLICE_TOPOLOGY per replica, admitted by Kueue
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/docs/infrastructure/providers/gke/dynamic-slicing/kueue-localqueue.yaml
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TPU_SLICE_TOPOLOGY}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.dynamic_slice end -->

With `ACCELERATOR_TYPE=tpu/v7-dynamic-slice`, the model servers run on dynamically formed TPU7x sub-slices instead of a static node pool per TPU topology. Capacity is pre-provisioned as `4x4x4` sub-blocks, and [GKE dynamic slicing](../../docs/infrastructure/providers/gke/dynamic-slicing/README.md) forms one sub-slice per model server replica at scheduling time, via Kueue Topology-Aware Scheduling. Each replica is a `LeaderWorkerSet` group; the GKE slice controller activates the requested sub-slice shape for the group and re-forms it on healthy partitions after a hardware failure.

Pick the sub-slice shape with `TPU_SLICE_TOPOLOGY`:

| `TPU_SLICE_TOPOLOGY` | Chips | Hosts (LWS `size`) | TP | Model |
| --- | --- | --- | --- | --- |
| [`2x2x1`](./modelserver/tpu/v7-dynamic-slice/vllm/2x2x1/) (default) | 4 | 1 | 8 | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` |
| [`2x2x2`](./modelserver/tpu/v7-dynamic-slice/vllm/2x2x2/) | 8 | 2 | 16 | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` |

Both shapes serve `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`, the model these recipes were load-tested with; set `MODEL` to it. To target `2x2x4` (4 hosts, TP up to 32) or `2x4x4` (8 hosts, TP up to 64), copy the `2x2x2` overlay and change the `cloud.google.com/gke-tpu-slice-topology` annotation, the `cloud.google.com/gke-tpu-partition-<shape>-state` node selector, the LWS `size`, and `--tensor-parallel-size` (2 cores per chip).

Before deploying the model servers, complete the cluster and Kueue TAS setup in [TPU Dynamic Slicing on GKE](../../docs/infrastructure/providers/gke/dynamic-slicing/README.md). The deploy step above also creates the Kueue `LocalQueue` in the guide namespace. Workloads are admitted once their `Slice` resources are `ACTIVE`:

```bash
kubectl get workloads -n ${NAMESPACE}
kubectl get slices -n ${NAMESPACE}
kubectl get pods -n ${NAMESPACE}
```

Notes:

* Increasing `spec.replicas` on the `LeaderWorkerSet` scales out one sub-slice per replica; replicas are formed from any sub-block with healthy partitions of the requested shape.
* Different shapes (and the [P/D dynamic-slice recipes](../pd-disaggregation/modelserver/tpu/v7-dynamic-slice/vllm/base/)) can share the same node pools and `ClusterQueue`; this is the primary utilization benefit over static per-topology node pools.
* On failure of a host in a multi-host group, `RecreateGroupOnPodRestart` restarts the group and the slice controller re-forms the sub-slice on healthy partitions.
* Not yet covered by nightly E2E: a run needs at least one full TPU7x cube (a `4x4x4` sub-block of 64 chips, 16 `tpu7x-standard-4t` nodes) in an All Capacity mode reservation. Until then the overlays are validated by kustomize dry-run in CI and by load tests on internal Google Cloud capacity during the dynamic-slicing beta.

</details>
<!-- tabs:end -->

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### 3. Observability & Troubleshooting

Once monitoring is enabled, use the signals below to operate the optimized baseline. This section covers the metrics that matter **for this path** and how to read them; full metric definitions live in the [metric reference](../../docs/operations/observability/metrics.md#metric-reference) and ready-to-run queries in the [PromQL reference](../../docs/operations/observability/promql.md).

This path is defined by its two routing objectives: **prefix-cache affinity** (route to endpoints that already hold the prompt prefix) and **load-aware** balancing (spread work by token load), with a saturation override that trades cache locality for spread once endpoints get hot. Most issues show up as those two objectives pulling against each other, so watch **load balance** and **cache hit rate** together rather than either one alone.

#### Key metrics for this path

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

| Signal | Why it matters for the optimized baseline | Where to look |
|--------|-------------------------------------------|---------------|
| Per-pod load (`llm_d_epp_request_total`, `vllm:num_requests_running`) | The load-aware scorer should keep QPS and active requests roughly even across pods. A persistently hot pod next to idle ones means balancing is not taking effect | [PromQL → Routing & Load Balancing](../../docs/operations/observability/promql.md#routing--load-balancing) |
| Prefix cache hit rate (`vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`) | The prefix-affinity filter is only helping if hit rate stays high. A falling ratio means requests are not landing on sticky endpoints | [PromQL → Prefix Caching](../../docs/operations/observability/promql.md#prefix-caching) |
| Per-pod KV cache utilization and queue depth (`vllm:kv_cache_usage_perc`, `vllm:num_requests_waiting`) | These drive the saturation-aware override. If one pod sits near saturation while others are cold, the override is either not firing or mis-tuned | [PromQL → Basic Model Serving](../../docs/operations/observability/promql.md#basic-model-serving) |
| Routing decision latency (`llm_d_epp_plugin_duration_seconds`) | Rising scheduler latency with healthy model servers localizes the problem to the routing layer, not the pods | [PromQL → Routing & Load Balancing](../../docs/operations/observability/promql.md#routing--load-balancing) |
| TTFT and ITL (`vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`) | The user-facing SLO signals this path is tuned to protect. Regressions here are the trigger to inspect the balance/cache split above | [Metrics → vLLM](../../docs/operations/observability/model-server-metrics.md#vllm) |

</details>
<details>
<summary><b>SGLang</b></summary>

SGLang deployments expose the equivalent signals under `sglang_*`; the [PromQL reference](../../docs/operations/observability/promql.md) lists both engines.

| Signal | Why it matters for the optimized baseline | Where to look |
|--------|-------------------------------------------|---------------|
| Per-pod load (`llm_d_epp_request_total`, `sglang_num_running_reqs`) | The load-aware scorer should keep QPS and active requests roughly even across pods. A persistently hot pod next to idle ones means balancing is not taking effect | [PromQL → Routing & Load Balancing](../../docs/operations/observability/promql.md#routing--load-balancing) |
| Prefix cache hit rate (`sglang_cache_hit_rate`) | The prefix-affinity filter is only helping if hit rate stays high. A falling ratio means requests are not landing on sticky endpoints | [PromQL → Prefix Caching](../../docs/operations/observability/promql.md#prefix-caching) |
| Per-pod KV cache utilization (`sglang_token_usage`) | Drives the saturation-aware override. If one pod sits near saturation while others are cold, the override is either not firing or mis-tuned | [PromQL → Basic Model Serving](../../docs/operations/observability/promql.md#basic-model-serving) |
| Routing decision latency (`llm_d_epp_plugin_duration_seconds`) | Rising scheduler latency with healthy model servers localizes the problem to the routing layer, not the pods | [PromQL → Routing & Load Balancing](../../docs/operations/observability/promql.md#routing--load-balancing) |

</details>
<details>
<summary><b>TensorRT-LLM</b></summary>

`trtllm-serve` exposes the equivalent load and KV-cache gauges (`trtllm_num_requests_running`, `trtllm_num_requests_waiting`, `trtllm_kv_cache_utilization`) at `/prometheus/metrics`; see the [model server requirements](../../docs/architecture/core/model-servers.md) for the flags that enable them.

</details>
<!-- tabs:end -->

#### Common failure modes

- **Uneven load across pods** (some hot, some idle) — the load-aware scorer or the saturation override is not spreading work. On **non-default hardware** this usually means `peakPrefillThroughput` is miscalibrated, so the override never gates in; measure it with the [calibration recipe](../recipes/router/calibration/README.md) and set it on the filter.
- **Low prefix cache hit rate** — either the prompt mix is not prefix-sticky, or the saturation override is spreading so aggressively that it defeats affinity. Compare hit rate against per-pod saturation; if pods are cold but hit rate is still low, the issue is the prompt pattern, not the override.
- **TTFT/ITL regression with balanced load and healthy cache** — look at routing decision latency and model-server queue depth before touching the routing config; the bottleneck is likely the model servers, not the scheduler.

For alert rules covering these signals, see [Alerting](../../docs/operations/observability/alerting.md).

## Verification

### 1. Get the IP of the Proxy

<!-- tabs:start group=mode -->
<details open>
<summary><b>Standalone Mode</b></summary>

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

</details>
<details>
<summary><b>Gateway Mode</b></summary>

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

</details>
<!-- tabs:end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster (model-aware; `MODEL` must be the model your accelerator serves, see the **Served model** column in [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)):**

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

### 3. Verify prefix-cache aware routing

A served request only proves the stack is up. To confirm the routing mechanism this guide deploys is engaged, send a burst of requests that share a long prompt prefix, then check that the router kept them on the model server that already caches that prefix.

**Send 10 requests that share a ~2k-token prefix:**

<!-- guide:verify.tests.shared_prefix start -->
```bash
# 10 requests that share a ~2k-token prefix and differ only in the last sentence
kubectl run prefix-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 100); do printf "The llm-d router keeps a conversation on the server that already caches its prefix. "; done)
    for i in $(seq 1 10); do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question ${i}: what is llm-d?\", \"max_tokens\": 16}"
    done'
```
<!-- guide:verify.tests.shared_prefix end -->

**Read the prefix-cache and request counters** of every model server pod:

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Prefix-cache and request counters of every model server pod, read
# through the Kubernetes API server proxy (no port-forward needed)
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^(vllm:prefix_cache_(hits|queries)_total|vllm:request_success_total|sglang:(cache_hit_rate|num_requests_total))' || true
done
```
<!-- guide:verify.tests.pod_metrics end -->

What to expect:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

One pod reports most of the 10 requests in `vllm:request_success_total`, and its `vllm:prefix_cache_hits_total` is a large fraction of `vllm:prefix_cache_queries_total` (every request after the first reuses the cached prefix). The other pods show few or none of these requests.

</details>
<details>
<summary><b>SGLang</b></summary>

One pod reports most of the requests in `sglang:num_requests_total`, and its `sglang:cache_hit_rate` is well above zero.

</details>
<details>
<summary><b>TensorRT-LLM</b></summary>

The command above reads vLLM and SGLang counters only. For `trtllm-serve`, query `/prometheus/metrics` on each pod instead (replace `:8000/proxy/metrics` with `:8000/proxy/prometheus/metrics`) and compare `trtllm_num_requests_running` across pods.

</details>
<!-- tabs:end -->

If the requests are spread evenly and hit rates stay near zero, prefix-cache affinity is not taking effect; see [Common failure modes](#common-failure-modes). Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## Cleanup

To remove the deployed components:

<!-- tabs:start group=modelserver -->
<details open>
<summary><b>Default</b></summary>

<!-- guide:cleanup.modelserver.standard start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver.standard end -->

</details>
<details data-when="ACCELERATOR_TYPE=tpu/v7-dynamic-slice">
<summary><b>Google TPU v7 (dynamic slicing)</b></summary>

<!-- guide:cleanup.modelserver.dynamic_slice start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TPU_SLICE_TOPOLOGY}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.modelserver.dynamic_slice end -->

</details>
<!-- tabs:end -->

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
