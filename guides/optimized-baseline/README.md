# Optimized Baseline

[![E2E (AMD ROCM)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-amd-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-amd-acc-rocm-vllm-x.yaml)
[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-ibm-acc-gpu-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-optimized-baseline-intel-acc-xpu-vllm-x.yaml)

## Overview

This guide deploys the recommended out of the box [configuration](https://github.com/llm-d/llm-d-router/blob/main/docs/architecture.md) for most vLLM, SGLang, and TensorRT-LLM deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing.

The optimized-baseline defaults to two main routing criteria:

- **Prefix-cache aware** using the [prefix cache affinity filter](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/filter/prefixcacheaffinity/README.md), which narrows candidates to "sticky" endpoints with high estimated prompt prefix cache reuse, with a saturation-aware override that spreads load when endpoints get hot.

- **Load-aware** using the [token load scorer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/tokenload/README.md), which scores endpoints based on the total prefill token load handled by each model server.

Both plugins are used with their built-in defaults — no per-deployment tuning is required for this guide's reference setup (Qwen3-32B on H100 80&nbsp;GB, TP=2). If you deploy a **different model or accelerator**, the saturation-aware override gate keys off the `peakPrefillThroughput` of the filter, which is hardware- and model-specific; measure your own with the shared [calibration recipe](../recipes/router/calibration/README.md) and set it on the filter. See [Adapting to other hardware](#adapting-to-other-hardware) below.

## Configuration

| Parameter          | Default                                                 | Example                                                 |
| ------------------ | ------------------------------------------------------- | --------------------------------------------------------- |
| Model              | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) | [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) |
| Replicas           | 8                                                       | 16                                                        |
| Tensor Parallelism | 2                                                       | 1                                                         |
| GPUs per replica   | 2                                                       | 1                                                         |
| Total GPUs         | 16                                                      | 16                                                        |

### Supported Hardware Backends

This guide includes configurations for the following accelerators:

| Backend             | Directory          | Notes                                      |
| ------------------- | ------------------ | ------------------------------------------ |
| NVIDIA GPU          | `gpu`              | Default configuration (`INFRA_PROVIDER` options: `base`, `gke`) |
| AMD GPU             | `amd`              | AMD GPU                                    |
| Intel XPU           | `xpu`              | Intel Data Center GPU Max 1550+            |
| Google TPU v6e      | `tpu/v6`           | GKE TPU                                    |
| Google TPU v7       | `tpu/v7`           | GKE TPU                                    |
| CPU                 | `cpu`              | x86 with bf16 acceleration — AMX or AVX512-BF16 (Intel Sapphire Rapids+ / GCP C3, AMD Zen 4+); 64 cores + 64GB RAM per replica. Older CPUs without AMX/AVX512-BF16 (e.g. Cascade/Ice Lake) crash on the bf16 model unless run with `--dtype=float32`. |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks. These configurations are maintained by their respective hardware vendors and are not guaranteed as production-ready examples. Users deploying on non-default hardware should review and adjust the configurations for their environment.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators to satisfy the [Configuration](#configuration) table above (default: 16 GPUs). If your cluster has fewer resources, adjust `replicas` and `--tensor-parallel-size` in the [model server patch](./modelserver/gpu/vllm/base/patch-vllm.yaml) for your environment.

- Set the branch and clone the llm-d repo:

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

- Set the guide specific environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=optimized-baseline
export NAMESPACE=llm-d-optimized-baseline
```
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
export MONITORING_VALUES=
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, hpu, tpu/v6, tpu/v7, cpu
export MODEL_SERVER=vllm # options: vllm, sglang, trtllm
export INFRA_PROVIDER=base # options: base, gke
export MODEL=Qwen/Qwen3-32B
export CURL_TEST_IMAGE=cfmanteiga/alpine-bash-curl-jq:latest
export BENCHMARK_REF=main
export HARNESS=inference-perf
export WORKLOAD=guide_optimized-baseline_1.yaml
export GATEWAY_CLASS=epponly # options: epponly, gke, agentgateway, istio
```
<!-- guide:env.static end -->

> [!NOTE]
> `HF_TOKEN` must be a [valid HuggingFace token](../../helpers/hf-token.md); replace
`HF_TOKEN_PLACEHOLDER` with your real token:

- Source the common guide environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

> [!NOTE]
> This file defines shared variables required by subsequent steps, including
> `GAIE_VERSION`, `ROUTER_CHART_VERSION`, and the router chart reference for
> the selected deployment mode.

- Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

- Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models:

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

- Prepare the paths to the `helm` values files for `llm-d` router (used in the deployment commands below):

<!-- guide:deploy.router_values start -->
```bash
export ROUTER_BASE_VALUES="-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml"

# only when MODEL_SERVER=vllm or sglang:
export ROUTER_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"

# only when MODEL_SERVER=trtllm:
#
# Comment out the above `ROUTER_VALUES` and uncomment the below for TensorRT-LLM (trtllm-serve)
#
# export ROUTER_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}-trtllm.values.yaml"
```
<!-- guide:deploy.router_values end -->

> [!NOTE]
> As denoted above, **vllm, sglang** share a values file, while
> **TensorRT-LLM** (`trtllm-serve`) has it's own values file.

- Optionally, to enable `Prometheus Monitoring` on the `llm-d` router define the `helm` values file:

<!-- guide:deploy.monitoring_values start -->
```bash
#
# Uncomment the below to enable Prometheus monitoring on the llm-d router
#
# export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

> [!NOTE]
> When following the guide from top to bottom, we already have `export MONITORING_VALUES=""` by default. This means that `monitoring` is disabled by default.

> [!WARNING]
> Enabling monitoring here requires the monitoring stack to be installed first. The
> `monitoring.values.yaml` file creates a `ServiceMonitor`, which needs the Prometheus
> Operator CRDs. Deploying the router with this file before the monitoring stack is ready
> (see [Step 3: Enable monitoring](#3-optional-enable-monitoring)) will fail with a Helm
> validation error. If you want monitoring enabled from the start, install the monitoring
> stack before the router, or leave `MONITORING_VALUES` empty and `helm upgrade` with the
> monitoring values after Step 3.

#### Standalone Mode

This deploys the llm-d Router in [Standalone Mode](../../docs/architecture/core/router/proxy.md) with an Envoy sidecar (default):

> [!IMPORTANT]
> Before running the command below, execute the path setup commands from the previous section: the `export ROUTER_BASE_VALUES=...` and `export ROUTER_VALUES=...` commands above.

<!-- guide:deploy.standalone start -->
```bash
# Assuming base-directory is the root of the llm-d repo
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

To use **agentgateway** as the sidecar proxy instead of Envoy, see [router recipes](../recipes/router/README.md).

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. _Deploy a Kubernetes Gateway_ named by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. _Deploy the llm-d router and an HTTPRoute_ that connects it to the Gateway as follows:

> [!IMPORTANT]
> Before running the command below, execute the path setup commands from the previous section: the `export ROUTER_BASE_VALUES=...` and `export ROUTER_VALUES=...` commands above.

<!-- guide:deploy.gateway start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  ${ROUTER_VALUES} \
  --set provider.name=${PROVIDER_NAME} \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.gateway end -->

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend:

<!-- guide:deploy.modelserver start -->
```bash
# only when ACCELERATOR_TYPE=gpu:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/

# only when ACCELERATOR_TYPE=amd or xpu or hpu or tpu/v6 or tpu/v7 or cpu:
#
# Comment out the above `kubectl apply` and uncomment the below to run on `NON GPU` accelerators
#
# kubectl apply -n ${NAMESPACE} \
#  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/
#
```
<!-- guide:deploy.modelserver end -->

> [!NOTE]
> Ensure `INFRA_PROVIDER`, `ACCELERATOR_TYPE` and `MODEL_SERVER` are set appropriately, see above environment section for options.

<details>
<summary><h4>Other Models</h4></summary>

For example to deploy other models:

```bash
# NVIDIA GPU / vLLM — openai/gpt-oss-120b
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/gpt-oss/
```

</details>

### 3. (Optional) Enable monitoring

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).

- This requires `Prometheus Monitoring` to be enabled on `llm-d` router, see `Step 1 Deploy the llm-d Router` above.

- Deploy the monitoring resources for model servers:

<!-- guide:deploy.monitoring start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

## Adapting to other hardware

The routing plugins ship with defaults tuned for this guide's reference setup (Qwen3-32B on H100 80&nbsp;GB, TP=2), so **no calibration is needed to run the guide as written**.

The saturation-aware override gate in the `prefix-cache-affinity-filter` keys off the filter's `peakPrefillThroughput` parameter, which is **hardware- and model-specific** (the plugin default, `15928`, is the value measured for this guide's setup). If you deploy a different model or accelerator, measure your own value with the shared calibration recipe and set it on the filter:

```yaml
# guides/optimized-baseline/router/optimized-baseline.values.yaml
- type: prefix-cache-affinity-filter
  parameters:
    peakPrefillThroughput: <measured value>
```

The recipe (`calibrate.sh`) runs a short Kubernetes Job that measures true prefill throughput against your live deployment and prints the value — it does not modify any config. See the recipe's README for full usage:

- [`guides/recipes/router/calibration/README.md`](../recipes/router/calibration/README.md)

For reference values across the (model, accelerator) combinations shipped under `guides/` — and which ones still need a calibration run — see the [**configuration matrix**](../recipes/router/calibration/configuration-matrix.md).

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

</details>

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster (model-aware; set `MODEL` to the name you want to query, e.g. `Qwen/Qwen3-32B` or `openai/gpt-oss-120b`):**

<!-- guide:verify.tests start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests end -->

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a shared-prefix synthetic workload against the stack you just deployed exactly as written above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more indepth explanation and features for benchmarking llm-d guides directly can be found at [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **optimized-baseline-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to exercise the optimized-baseline routing under realistic load — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

### 1. Install the `llmdbenchmark` CLI

Automatically clone the benchmark repository into `./llm-d-benchmark/` and create a virtualenv at `./llm-d-benchmark/.venv/` containing dependencies and it's installation:

<!-- guide:benchmark.setup start -->
```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/${BENCHMARK_REF}/install.sh | bash

cd llm-d-benchmark

source .venv/bin/activate

llmdbenchmark --version
```
<!-- guide:benchmark.setup end -->

> [!NOTE]
> Subsequent `llmdbenchmark` commands in this section assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the two commands above.

### 2. Resolve the endpoint of the stack you just deployed

Set two variables so the rest of the section is topology-agnostic: the endpoint URL and the gateway class. The gateway class tells the CLI which deployment topology the cluster is actually running, without this, the CLI re-renders against the benchmark scenario's default values.

**Standalone Mode** (the default in this guide — no Kubernetes Gateway, EPP pod with an Envoy sidecar). `GATEWAY_CLASS=epponly` is the default:

<!-- guide:benchmark.endpoint.standalone start -->
```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
```
<!-- guide:benchmark.endpoint.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

Set `GATEWAY_CLASS` to match whichever provider you used when deploying the gateway (e.g. `istio`, `agentgateway`, `gke`) — see the `GATEWAY_CLASS` options in the [environment section](#prerequisites) above.

<!-- guide:benchmark.endpoint.gateway start -->
```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"
```
<!-- guide:benchmark.endpoint.gateway end -->

</details>

### 3. Run the benchmark profile for Optimized Baseline

`guide_optimized-baseline_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the load ladder used to generate the [graphs at the bottom of this guide](#benchmarking-report) (rates 3 to 60) and is shaped to highlight the strengths of the optimized-baseline routing under realistic saturation.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

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

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.
> Model-aware; set `model` to the name you want to query, e.g. `Qwen/Qwen3-32B` or `openai/gpt-oss-120b`

## Cleanup

To remove the deployed components:

<!-- guide:cleanup start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

# only when ACCELERATOR_TYPE=gpu:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}

# only when ACCELERATOR_TYPE=amd or xpu or hpu or tpu/v6 or tpu/v7 or cpu:
#
# Comment out the above `kubectl delete` and uncomment the below to run on `NON GPU` accelerators
#
# kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->

## Benchmarking Reports

Empirical benchmark reports comparing llm-d routing performance against a standard Kubernetes Service under identical hardware configurations:

- [Qwen/Qwen3-32B on H100 and SGLang](./benchmark-results/sglang-qwen3-32b-h100/README.md)
- [Qwen/Qwen3-32B on H100 and vLLM](./benchmark-results/vllm-qwen3-32b-h100/README.md)
- [openai/gpt-oss-120b on H100 and vLLM](./benchmark-results/vllm-gpt-oss-120b-h100/README.md)
