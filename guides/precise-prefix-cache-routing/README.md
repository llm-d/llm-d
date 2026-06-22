# Precise Prefix Cache Routing

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml)

## Overview

This guide routes requests on precise per-pod KV-cache state rather than request-traffic heuristics. Each vLLM pod publishes [KV-cache events](https://github.com/vllm-project/vllm/issues/16669) over ZMQ; the router subscribes, builds an index keyed by block hash, and scores candidate pods by the fraction of an incoming request's prefix that is already resident.

Two scorers make up the routing decision alongside the load-aware stack:

- **Precise prefix-cache aware** — the [precise-prefix-cache-producer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/dataproducer/preciseprefixcache) indexes real KV-block events from vLLM and publishes the exact resident-block fraction. The generic [prefix-cache-scorer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/prefix) then reads `prefixMatchInfoProducerName`. Indexer internals (event ingestion, block hashing, dual-key design) are documented in [llm-d-kv-cache architecture](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/architecture.md).
- **Load-aware** — such as the [kv-cache utilization](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/kvcacheutilization) and [queue size](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/queuedepth) scorers balance against pod pressure.

## Default Configuration

| Parameter           | Value                                                   |
|---------------------|---------------------------------------------------------|
| Model               | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas            | 8 (reduce for smaller fleets — see notes below)         |
| Tensor Parallelism  | 2                                                       |
| GPUs per replica    | 2                                                       |
| Total GPUs          | 16                                                      |
| vLLM `--block-size` | 64 (must match scorer `tokenProcessorConfig.blockSize`) |

### Supported Hardware Backends

| Backend              | Directory                  | Default model                           | Notes                                                    |
| -------------------- | -------------------------- | --------------------------------------- | -------------------------------------------------------- |
| NVIDIA GPU           | `modelserver/gpu/vllm/`    | Qwen/Qwen3-32B                          | Default configuration                                    |
| NVIDIA GPU (SGLang)  | `modelserver/gpu/sglang/`  | Qwen/Qwen3-32B                          | SGLang; `--page-size=64` matches scorer `blockSize`      |
| AMD GPU              | `modelserver/amd/vllm/`    | Qwen/Qwen3-32B                          | AMD GPU                                                  |
| Intel XPU            | `modelserver/xpu/vllm/`    | Qwen/Qwen3-0.6B                         | CI-sized; update router `modelName` for real use         |
| Google TPU v6e       | `modelserver/tpu/v6/vllm/` | Qwen/Qwen3-32B                          | GKE TPU                                                  |
| Google TPU v7        | `modelserver/tpu/v7/vllm/` | Qwen3-Coder-480B-FP8                    | GKE TPU                                                  |
| CPU                  | `modelserver/cpu/vllm/`    | Llama-3.2-3B-Instruct                   | CI-sized                                                 |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks.
>
> [!NOTE]
> For precise prefix cache scoring to match reality, the `token-producer` `modelName` in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml) must match the model the overlay deploys.
>
> [!NOTE]
> The `gpu/vllm/` overlay defaults to 8 replicas to match the canonical 16×H100 benchmark. For smaller fleets (or quick smoke tests), reduce `replicas` in the deployment patch (`modelserver/gpu/vllm/patch-vllm.yaml`) before applying.
>
> [!NOTE]
> The router runs in **active-active HA** by default — two replicas behind one Service, each subscribing to every vLLM pod via pod-discovery so both indexes converge. Scale to a single replica with `--set router.epp.replicas=1` if HA isn't needed (small fleets, smoke tests).

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Checkout llm-d repo:

```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
```

- Set the following environment variables:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export GUIDE_NAME="precise-prefix-cache-routing"
export NAMESPACE="llm-d-${GUIDE_NAME}"
```

- Install the Gateway API Inference Extension CRDs:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```

- Create a target namespace for the installation

```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```

## Installation Instructions

### 1. Prepare HF Token

Create the `llm-d-hf-token` secret in the namespace. The router reads `HF_TOKEN` to reach gated tokenizers — Qwen/Qwen3-32B is public but the secret makes swapping in a gated model a no-op. See [helpers/hf-token.md](../../helpers/hf-token.md) for the full helper.
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=<your HuggingFace token>
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->

### 2. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router in the simple [Standalone Mode](../../docs/architecture/core/router/proxy.md). The release name `${GUIDE_NAME}` is mandatory — the inference pool selector matches a guide label that pairs with this release.

The chart auto-injects the `vllm-render` sidecar when `router.tokenizer.enabled: true` is set in the values file.

```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

The release name `${GUIDE_NAME}` is mandatory for standard deployments — the inference pool selector matches a guide label that pairs with this release.

<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy instead of the standalone Envoy sidecar, do **not** apply the standalone chart above. Instead:

1. **Deploy a Kubernetes Gateway**. See [the gateway guides](../../docs/infrastructure/gateway) for step-by-step deployment of a Gateway named `llm-d-inference-gateway`.

2. **Deploy the llm-d Router and HTTPRoute** via the `llm-d-router-gateway` chart with `httpRoute.create=true`:

```bash
export PROVIDER_NAME=istio   # options: none, gke, agentgateway, istio

helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  --set provider.name=${PROVIDER_NAME} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 3. Deploy the Model Server

Apply the Kustomize overlay for your backend (defaulting to NVIDIA GPU / vLLM):

```bash
export MODEL_SERVER=vllm # vllm | sglang
export INFRA_PROVIDER=base # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/${MODEL_SERVER}/${INFRA_PROVIDER}/
```

### 4. (Optional) Enable Monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/operations/observability/setup.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).
- Deploy the monitoring resources for this guide:

  ```bash
  kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
  ```

- Enable Prometheus scrape for the router by layering `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` onto the helm command in step 2.

## Verification

### 1. Get the IP of the Proxy

#### Standalone Mode

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-32B",
        "prompt": "How are you today?"
    }' | jq
```

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a shared-prefix synthetic workload against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **precise-prefix-cache-routing-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to stress the prefix-cache routing decision under contention — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

### 1. Install the `llmdbenchmark` CLI

Automatically clone the benchmark repository into `./llm-d-benchmark/` and create a virtualenv at `./llm-d-benchmark/.venv/` containing dependencies and its installation:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
```

Activate the `venv` and enter the repository directory - both are required: the `venv` puts `llmdbenchmark` on your PATH, and the repository directory contains the `workload/profiles/` and `config/specification/` files that orchestrate the benchmark:

```bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```

> [!NOTE]
> Subsequent `llmdbenchmark` commands in this section assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the two commands above.

### 2. Resolve the endpoint of the stack you just deployed

Set two variables so the rest of the section is topology-agnostic: the endpoint URL and the gateway class. The gateway class tells the CLI which deployment topology the cluster is actually running, without this, the CLI re-renders against the benchmark scenario's default values.

**Standalone Mode** (the default in this guide — no Kubernetes Gateway, EPP pod with an Envoy sidecar):

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly # standalone mode
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"

# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

</details>

### 3. Run the benchmark profile for Precise Prefix Cache Routing

`guide_precise-prefix-cache-routing_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the load ladder used to generate the [graphs at the bottom of this guide](#benchmarking-report) (rates 3 to 60 across 150 distinct prefix groups) and is shaped to highlight the strengths of precise prefix-cache routing by stressing the routing decision under contention.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/precise-prefix-cache-routing \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "Qwen/Qwen3-32B" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_precise-prefix-cache-routing_1.yaml \
    --analyze
```

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.
> [!IMPORTANT]
> When benchmarking TPU v6e or configurations with strict context length limits (e.g., `--max-model-len=4096` as in the default `patch-vllm.yaml` for TPU v6e), you **must** update the workload parameters inside `guide.yaml` before running.
> Specifically, decrease `system_prompt_len` (e.g. to `2000`), `question_len` (e.g. to `500`), and `output_len` (e.g. to `500`) so that the total request context size (`3000` tokens) stays well below the model's `4096` max token length limit. Leaving the default `6000`/`1200` values will cause the vLLM engine to reject all benchmark requests with `400 Bad Request`.

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
# For vLLM (default):
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
# For SGLang:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/
```

## How It Works

1. **Model server pods publish KV-cache events** — each pod (vLLM or SGLang) runs with `--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'` and `KV_EVENTS_ENDPOINT=tcp://*:5556`, binding its own ZMQ socket. On every KV block allocation/eviction, the server emits a ZMQ message.
2. **Router subscribes per pod** — pod-discovery (`kvEventsConfig.discoverPods: true`) registers the `precise-prefix-cache-producer` as an extractor on the data-layer `endpoint-notification-source`, so each router replica installs a ZMQ subscriber per model server pod independently. All replicas converge to the same index.
3. **Scoring** — the `prefix-cache-scorer` returns the fraction of the request's prefix blocks that are resident on each candidate pod. The `max-score-picker` routes to the highest-scoring pod.

## Benchmarking Report

### vLLM

The benchmark runs on 16× H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

#### Comparing llm-d Scheduling to a Simple Kubernetes Service

Graphs below compare the precise path to a stock Kubernetes Service that round-robins requests across the same 8 vLLM pods (no EPP, no scoring).

<img src="./benchmark-results/throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./benchmark-results/latency_vs_qps.png" width="900" alt="Latency vs QPS">
<img src="./benchmark-results/ttft_p90_vs_qps.png" width="900" alt="TTFT p90 vs QPS">

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | llm-d Precise | Δ% vs k8s |
| :------------------ | :--------------- | :------------ | :-------- |
| Output tokens/sec   | 5,722            | 12,598        | +120.2%   |
| Requests/sec        | 35.87            | 36.01         | +0.4%     |
| TTFT mean (s)       | 58.10            | 0.247         | −99.57%   |
| TTFT p90 (s)        | 107.43           | 0.262         | −99.76%   |
| ITL mean (ms)       | 44.0             | 47.0          | +6.8%     |

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,797      | 1,707        | 0.415         | 0.155           | 0.522        | 0.187          |
| 10   | 4,215      | 4,904        | 0.630         | 0.150           | 1.014        | 0.199          |
| 15   | 5,381      | 6,887        | 0.881         | 0.155           | 1.593        | 0.225          |
| 20   | 6,205      | 11,224       | 18.103        | 0.206           | 35.344       | 0.320          |
| 22   | 5,517      | 11,980       | 20.171        | 0.152           | 39.436       | 0.191          |
| 25   | 5,965      | 12,548       | 21.842        | 0.158           | 42.813       | 0.200          |
| 30   | 5,702      | 13,507       | 24.597        | 0.155           | 46.036       | 0.193          |
| 35   | 5,890      | 13,803       | 24.162        | 0.157           | 45.190       | 0.202          |
| 40   | 6,336      | 15,593       | 68.673        | 0.494           | 126.238      | 0.272          |
| 43   | 6,588      | 15,612       | 72.429        | 0.422           | 130.275      | 0.265          |
| 46   | 6,459      | 15,462       | 70.084        | 0.257           | 129.810      | 0.273          |
| 49   | 6,265      | 15,607       | 70.659        | 0.200           | 133.718      | 0.267          |
| 52   | 6,303      | 15,728       | 74.326        | 0.208           | 134.981      | 0.279          |
| 55   | 6,290      | 15,612       | 72.564        | 0.199           | 134.034      | 0.272          |
| 57   | 6,089      | 15,667       | 72.329        | 0.211           | 135.023      | 0.293          |
| 60   | 6,551      | 15,733       | 75.586        | 0.214           | 138.663      | 0.300          |

</details>

### SGLang

#### Comparing llm-d Scheduling to a Simple Kubernetes Service

Benchmark run with the canonical shared-prefix workload from this guide, comparing the precise path against a plain Kubernetes Service (round-robin, no EPP, no scoring) across the same SGLang pods. llm-d Precise shows a large improvement over the k8s baseline — roughly 2× output throughput with TTFT held near constant while the baseline degrades sharply under load.

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,752      | 1,690        | 0.629         | 0.213           | 1.014        | 0.243          |
| 10   | 4,377      | 5,013        | 0.926         | 0.213           | 1.655        | 0.295          |
| 15   | 4,528      | 6,983        | 3.593         | 0.202           | 5.630        | 0.323          |
| 20   | 5,799      | 9,439        | 26.500        | 0.529           | 60.170       | 0.511          |
| 22   | 4,803      | 9,671        | 29.185        | 1.050           | 63.806       | 0.567          |
| 25   | 5,559      | 9,772        | 29.125        | 0.985           | 63.950       | 0.650          |
| 30   | 4,967      | 9,976        | 34.295        | 0.759           | 73.831       | 0.573          |
| 35   | 5,816      | 9,962        | 34.191        | 0.972           | 73.837       | 0.596          |
| 40   | 5,548      | 11,964       | 84.999        | 15.505          | 152.473      | 44.448         |
| 43   | 5,374      | 12,049       | 87.271        | 17.201          | 157.532      | 52.145         |
| 46   | 5,374      | 11,989       | 87.138        | 19.652          | 156.928      | 56.452         |
| 49   | 5,692      | 11,744       | 85.618        | 18.361          | 157.622      | 52.982         |
| 52   | 5,326      | 11,933       | 87.259        | 20.287          | 160.448      | 56.957         |

</details>

> Benchmark contributed by @liu-cong.
