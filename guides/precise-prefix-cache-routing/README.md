# Precise Prefix Cache Routing

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-ibm-acc-gpu-vllm-x.yaml)
[![E2E (AMD ROCm)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-amd-ci-acc-rocm-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-precise-prefix-cache-routing-amd-ci-acc-rocm-vllm-x.yaml)

## Overview

This guide routes requests on precise per-pod KV-cache state rather than request-traffic heuristics. Each vLLM pod publishes [KV-cache events](https://github.com/vllm-project/vllm/issues/16669) over ZMQ; the router subscribes, builds an index keyed by block hash, filters candidates to the pods where an incoming request's prefix is already resident, and picks the least token-loaded pod within that set.

The routing decision combines precise cache knowledge with token-based load balancing:

- **Exact tokens** — the [`token-producer`](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/dataproducer/tokenizer) renders each prompt on the model servers themselves, so the router hashes the same token IDs the engine caches. Completions, Chat Completions and Anthropic Messages requests are all rendered by vLLM.
- **Precise prefix-cache aware** — the [precise-prefix-cache-producer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/dataproducer/preciseprefixcache) indexes real KV-block events from vLLM and publishes the exact resident-block fraction. The `prefix-cache-affinity-filter` reads it via `prefixMatchInfoProducerName` to keep each prefix group on its cache-warm endpoints, gated by a calibrated `peakPrefillThroughput` so saturated endpoints are bypassed. Indexer internals (event ingestion, block hashing, index design) are documented in the router's [`pkg/kvcache`](https://github.com/llm-d/llm-d-router/tree/main/pkg/kvcache).
- **Token-load aware** — the `token-load-scorer` (fed by the `inflight-load-producer`) picks the least token-loaded endpoint within the filtered set, balancing by queued prefill work rather than request counts.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas | 8 (reduce for smaller fleets — see notes below) |
| Tensor Parallelism | 2 |
| GPUs per replica | 2 |
| Total GPUs | 16 |
| vLLM `--block-size` | 64 (must match the `precise-prefix-cache-producer`'s `tokenProcessorConfig.blockSizeTokens`) |

### Supported Hardware Backends

| Backend | Directory | Default model | Notes |
| --- | --- | --- | --- |
| NVIDIA GPU | `modelserver/gpu/vllm/` | Qwen/Qwen3-32B | Default configuration |
| NVIDIA GPU (SGLang) | `modelserver/gpu/sglang/` | Qwen/Qwen3-32B | SGLang; `--page-size=64` matches the producer's `blockSizeTokens`; requires `render/standalone/` and `router/sglang.values.yaml` |
| AMD GPU | `modelserver/amd/vllm/` | Qwen/Qwen3-32B | AMD GPU |
| Intel XPU | `modelserver/xpu/vllm/` | Qwen/Qwen3-0.6B | CI-sized; update router `modelName` for real use |
| Google TPU v6e | `modelserver/tpu/v6/vllm/` | Qwen/Qwen3-32B | GKE TPU |
| Google TPU v7 | `modelserver/tpu/v7/vllm/` | Qwen3-Coder-480B-FP8 | GKE TPU |
| CPU | `modelserver/cpu/vllm/` | Llama-3.2-3B-Instruct | CI-sized |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks.
>
> [!NOTE]
> The `token-producer` `modelName` in [`router/precise-prefix-cache-routing.values.yaml`](router/precise-prefix-cache-routing.values.yaml) must match the model the overlay deploys. The router renders on the model servers themselves, so a mismatch is rejected outright rather than silently scoring against the wrong tokenizer.
>
> [!NOTE]
> The `gpu/vllm/` overlay defaults to 8 replicas to match the canonical 16×H100 benchmark. For smaller fleets (or quick smoke tests), reduce `replicas` in the deployment patch (`modelserver/gpu/vllm/patch-vllm.yaml`) before applying.
>
> [!NOTE]
> The router runs as a **single replica** by default: the `token-load-scorer`'s in-flight token accounting is local to each EPP process, so two active-active replicas would each see only half the per-endpoint load and mis-gate the affinity filter. The precise KV index itself is HA-safe (each replica converges independently via pod-discovery), so active-active HA (`--set router.epp.replicas=2`) can return if shared in-flight state lands upstream.

For wide-EP LWS deployments (multi-port DP model servers), use the
[`wide-ep` precise routing variant](../wide-ep/README.precise-prefix-cache-routing.md)
instead of the manifests here.

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
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
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

Tokenization runs on the model servers themselves: the `token-producer` plugin discovers them through the InferencePool and spreads render calls across them, so there is no render Service or EPP sidecar to deploy.

```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

The release name `${GUIDE_NAME}` is mandatory for standard deployments — the inference pool selector matches a guide label that pairs with this release.

<details>
<summary><b>SGLang</b></summary>

SGLang does not serve vLLM's render endpoints, so the router renders on a dedicated pool instead (deployed in [step 4](#4-check-rendering)). Layer [`router/sglang.values.yaml`](router/sglang.values.yaml) on top, in either mode:

<!-- llm-d-cicd:skip start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/sglang.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- llm-d-cicd:skip end -->

</details>

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

### 4. Check Rendering

The EPP `token-producer` plugin tokenizes prompts by calling vLLM's `/v1/*/render` endpoints. With `vllm.endpointDiscovery`, it sends each call to one of the Ready model servers in the InferencePool, round-robin, and retries once on another model server after a transport error, a timeout, or a 408, 429 or 5xx response. There is nothing to deploy for vLLM backends: render capacity scales with the model server fleet, which keeps render latency contained at higher QPS.

vLLM 0.30 serves `/v1/*/render` only with `--enable-scale-out`; the NVIDIA GPU, AMD GPU, Intel XPU, and CPU overlays set this flag. The TPU overlays currently use vLLM 0.29, which exposes the endpoint without the flag. Without it, every render call returns 404, vLLM logs `Scale-out endpoints are disabled`, and the router routes every request without cache information.

The trade-offs to weigh:

- Tokenization CPU competes with serving on the same pods. The GPU overlay requests 8 CPU / limits 16 per replica; raise that if render latency climbs under load.
- The render call is a synchronous hop to a serving pod on the request path.
- Only Ready endpoints receive render calls, so they never go to a pod still loading weights.

<details>
<summary><b>Dedicated renderer pool (required for SGLang)</b></summary>

SGLang does not implement vLLM's render endpoints, so the SGLang setup runs a dedicated, GPU-less `vllm launch render` pool (3 replicas) behind a Service, and the router reaches it through `vllm.url` in [`router/sglang.values.yaml`](router/sglang.values.yaml) (see [step 2](#2-deploy-the-llm-d-router)).

<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```
<!-- llm-d-cicd:skip end -->

Because this pool is GPU-less and engine-agnostic, it tokenizes with vLLM's tokenizer no matter which engine serves inference. It is configured for `Qwen/Qwen3-32B`; for another model, change the model argument in [`render/standalone/deployment.yaml`](render/standalone/deployment.yaml) and the router `token-producer` `modelName` together. Scale it with `kubectl scale -n ${NAMESPACE} deploy/${GUIDE_NAME}-render --replicas=<N>`.

> [!NOTE]
> These render pods deliberately do **not** carry the `llm-d.ai/guide` label — that label is the InferencePool / model-server selector, and the EPP would otherwise treat render pods as routable model servers (and try to subscribe to their nonexistent KV-event socket).

</details>

#### Verify rendering

Check that a model server returns token IDs before sending routed requests. Set `MODEL_NAME` to the model deployed by your overlay (for example, `Qwen/Qwen3-0.6B` on Intel XPU). For the SGLang setup, set `RENDER_URL=http://${GUIDE_NAME}-render:8000` instead.

```bash
MODEL_NAME=Qwen/Qwen3-32B
POD_IP=$(kubectl get pods -n ${NAMESPACE} \
  -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=decode \
  -o jsonpath='{.items[0].status.podIP}')
RENDER_URL=http://${POD_IP}:8000
kubectl run render-check --rm -i --restart=Never \
  --image=python:3.12-alpine --namespace="$NAMESPACE" \
  --env="MODEL_NAME=$MODEL_NAME" --env="RENDER_URL=$RENDER_URL" -- \
  python -c '
import json, os, urllib.request
data = json.dumps({"model": os.environ["MODEL_NAME"], "prompt": "render check", "max_tokens": 1}).encode()
request = urllib.request.Request(
    os.environ["RENDER_URL"] + "/v1/completions/render",
    data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=10) as response:
    body = json.load(response)
assert isinstance(body, list) and body and body[0].get("token_ids"), body
print(body[0]["token_ids"])
'
```

A successful completion alone does not show that routing uses the cache; [Confirm prefix-aware routing](#3-confirm-prefix-aware-routing) does.

### 5. (Optional) Enable Monitoring

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).
- To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#2-deploy-the-llm-d-router).
- Deploy the monitoring resources for model servers:

  ```bash
  kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
  ```

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

### 3. Confirm prefix-aware routing

This check reads the router's metrics, so it needs router monitoring from [step 5](#5-optional-enable-monitoring), which also turns off metrics authentication. From the `curl-debug` shell, send the same long prompt twice:

<!-- llm-d-cicd:skip start -->
```bash
PROMPT=$(for i in $(seq 1 300); do printf 'Prefix routing check sentence %s. ' "$i"; done)
for i in 1 2; do
  curl -s -o /dev/null -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d "{\"model\": \"Qwen/Qwen3-32B\", \"prompt\": \"${PROMPT}\", \"max_tokens\": 1}"
done
```

Then read the router's counters from your workstation:

```bash
kubectl port-forward -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp 9090:9090 &
curl -s localhost:9090/metrics | grep -E '^llm_d_epp_(prefix_cache_affinity_filter_decisions_total|kv_cache_index_max_pod_hit_count_total)'
```
<!-- llm-d-cicd:skip end -->

- `llm_d_epp_prefix_cache_affinity_filter_decisions_total{outcome="sticky"}` counts requests the affinity filter kept on cache-warm pods. The second request should add one.
- `llm_d_epp_kv_cache_index_max_pod_hit_count_total` counts the cached blocks the index matched. It should be above zero.

> [!WARNING]
> If every decision is `no_match` and the hit count stays at zero, the router is not seeing the engine's tokens and routing is load-only. Check that rendering works ([step 4](#4-check-rendering)) and that the router config declares a `token-producer` with a `vllm` backend. Without one, the router falls back to a byte-count estimate whose block keys never match the engine's, and nothing reports an error.

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a shared-prefix synthetic workload against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **precise-prefix-cache-routing-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

<!-- -->

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is
> intentionally shaped to stress the prefix-cache routing decision under
> contention — and accordingly takes longer to complete. To run a simpler
> workload with fewer execution cycles first (useful for validating the path,
> image pulls, PVC binding, etc. before committing to a real run), pick a
> generic sample profile such as `shared_prefix_synthetic.yaml` from the
> catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles)
> and substitute it for the `--workload` flag in the command below.

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

`guide_precise-prefix-cache-routing_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the load ladder used to generate the [graphs at the bottom of this guide](#benchmarking-reports) (rates 3 to 60 across 150 distinct prefix groups) and is shaped to highlight the strengths of precise prefix-cache routing by stressing the routing decision under contention.

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
# For SGLang, the model servers and the dedicated render pool:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/standalone/
```

## How It Works

1. **Model server pods publish KV-cache events** — each pod (vLLM or SGLang) runs with `--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'` and `KV_EVENTS_ENDPOINT=tcp://*:5556`, binding its own ZMQ socket. On every KV block allocation/eviction, the server emits a ZMQ message. The GPU vLLM backend (v0.26.0+) additionally binds a ZMQ ROUTER socket on port 5559 and retains the last 10,000 batches in an in-memory replay buffer for index recovery.
2. **Router subscribes per pod** — pod-discovery (`kvEventsConfig.discoverPods: true`) registers the `precise-prefix-cache-producer` as an extractor on the data-layer `endpoint-notification-source`, so each router replica installs a ZMQ subscriber per model server pod independently. All replicas converge to the same index. When a replay endpoint is available, each subscriber requests buffered events on first connect (or after an EPP restart) to rebuild its KV-block index without waiting for live traffic.
3. **Router tokenizes the prompt** — before it can look the prefix up in that index, the `token-producer` plugin POSTs the prompt to one of the model server pods' `/v1/*/render` endpoints to get exact token IDs, rotating across the Ready pods of the InferencePool. Anthropic Messages requests use vLLM's native `/v1/messages/render`. The router hashes the tokens into 64-token block keys (XXH64) and looks them up in the index.
4. **Filter + score** — the `prefix-cache-affinity-filter` narrows candidates to the pods where the request's prefix blocks are resident (falling back to the least-loaded pods when the cache-warm set is saturated past `peakPrefillThroughput`), and the `token-load-scorer` picks the endpoint with the least in-flight token load among them.

## Benchmarking Reports

Empirical benchmark reports comparing event-driven precise prefix-cache routing performance against a standard Kubernetes Service under identical hardware configurations:

- **[Qwen/Qwen3-32B on vLLM (16×H100 Precise Routing)](./benchmark-results/vllm-qwen3-32b-h100.md)**: Compares precise prefix routing against round-robin Kubernetes Service load balancing on vLLM.
- **[Qwen/Qwen3-32B on SGLang (16×H100 Precise Routing)](./benchmark-results/sglang-qwen3-32b-h100.md)**: Compares precise prefix routing against round-robin Kubernetes Service load balancing on SGLang.
