# Precise Prefix Cache Aware Routing

[![Nightly - Precise Prefix Cache E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml) [![Nightly - Precise Prefix Cache E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml) [![Nightly - Precise Prefix Cache E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml)

## Overview

This guide routes requests on precise per-pod KV-cache state rather than request-traffic heuristics. Each vLLM pod publishes [KV-cache events](https://github.com/vllm-project/vllm/issues/16669) over ZMQ; the scheduler subscribes, builds an index keyed by block hash, and scores candidate pods by the fraction of an incoming request's prefix that is already resident.

Two scorers make up the routing decision alongside the load-aware stack:

- **Precise prefix-cache aware** — the [precise-prefix-cache-scorer](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/preciseprefixcache) indexes real KV-block events from vLLM and returns the exact resident-block fraction. Indexer internals (event ingestion, block hashing, dual-key design) are documented in [llm-d-kv-cache architecture](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/architecture.md).
- **Load-aware** — such as the [kv-cache utilization](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/kvcacheutilization) and [queue size](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/queuedepth) scorers balance against pod pressure.

## Default Configuration

| Parameter           | Value                                                   |
|---------------------|---------------------------------------------------------|
| Model               | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas            | 8 (reduce for smaller fleets — see notes below)         |
| Tensor Parallelism  | 2                                                       |
| GPUs per replica    | 2                                                       |
| Total GPUs          | 16                                                      |
| vLLM `--block-size` | 64 (must match scorer `tokenProcessorConfig.blockSize`) |
| Scheduler image     | `ghcr.io/llm-d/llm-d-inference-scheduler:v0.8.0`        |

### Supported Hardware Backends

| Backend              | Directory                  | Default model                           | Notes                                      |
| -------------------- | -------------------------- | --------------------------------------- | ------------------------------------------ |
| NVIDIA GPU           | `modelserver/gpu/vllm/`    | Qwen/Qwen3-32B                          | Default configuration                      |
| AMD GPU              | `modelserver/amd/vllm/`    | Qwen/Qwen3-32B                          | AMD GPU                                    |
| Intel XPU            | `modelserver/xpu/vllm/`    | Qwen/Qwen3-0.6B                         | CI-sized; update scheduler `modelName` for real use |
| Intel Gaudi (HPU)    | `modelserver/hpu/vllm/`    | Qwen/Qwen3-8B                           | `--block-size=128`; update scorer `blockSize` to match |
| Google TPU v6e       | `modelserver/tpu-v6/vllm/` | Llama-3.1-70B-Instruct                  | GKE TPU                                    |
| Google TPU v7        | `modelserver/tpu-v7/vllm/` | Qwen3-Coder-480B-FP8                    | GKE TPU                                    |
| CPU                  | `modelserver/cpu/vllm/`    | Llama-3.2-3B-Instruct                   | CI-sized                                   |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks.

> [!NOTE]
> For precise prefix cache scoring to match reality, the `tokenizer` `modelName` and the scorer's `indexerConfig.tokenizersPoolConfig.modelName` in [`scheduler/precise-prefix-cache-aware.values.yaml`](scheduler/precise-prefix-cache-aware.values.yaml) must match the model the overlay deploys. HPU and anything that tunes `--block-size` also requires updating `tokenProcessorConfig.blockSize` on the scheduler side.

> [!NOTE]
> The `gpu/vllm/` overlay defaults to 8 replicas to match the canonical 16×H100 benchmark. For smaller fleets (or quick smoke tests), reduce `replicas` in the deployment patch (`modelserver/gpu/vllm/patch-vllm.yaml`) before applying.

> [!NOTE]
> The scheduler runs in **active-active HA** by default — two replicas behind one Service, each subscribing to every vLLM pod via pod-discovery so both indexes converge. Scale to a single replica with `--set inferenceExtension.replicas=1` if HA isn't needed (small fleets, smoke tests).

## Prerequisites

- Install the [Gateway API Inference Extension CRDs](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/v1.5.0/config/crd).
- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md). This guide requires **Helm v4** (the post-renderer plugin uses the v4 plugin manifest format) and a standalone `kustomize` binary (v5+) on `$PATH`, in addition to `kubectl`.
- Check out the llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

## Installation Instructions

### 1. Prepare a Target Namespace

```bash
export NAMESPACE=llm-d-precise
kubectl create namespace ${NAMESPACE}
```

Create the `llm-d-hf-token` secret in the namespace. The UDS tokenizer sidecar reads `HF_TOKEN` to reach gated tokenizers — Qwen/Qwen3-32B is public but the secret makes swapping in a gated model a no-op. See [helpers/hf-token.md](../../helpers/hf-token.md) for the full helper.

```bash
kubectl -n ${NAMESPACE} create secret generic llm-d-hf-token --from-literal=HF_TOKEN="${HF_TOKEN}"
```

### 2. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router in the simple [Standalone Mode](placeholder-link):

```bash
helm plugin install guides/precise-prefix-cache-aware/scheduler/patches/uds-tokenizer   # once
helm install precise-prefix-cache-aware \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
  --post-renderer uds-tokenizer \
  -n ${NAMESPACE} --version v1.5.0
```

The release name `precise-prefix-cache-aware` is mandatory for standard deployments — the inference pool selector matches a guide label that pairs with this release.

<details>
<summary><b>Why a helm post-renderer is required (chart limitation)</b></summary>

The standalone chart's `sidecar.*` slot is occupied by its Envoy proxy — overriding it would lose HTTP serving — so the UDS tokenizer container is appended via a helm post-render hook instead. The post-renderer runs `kustomize build` on the chart's rendered manifests with a strategic merge patch that adds the `tokenizer-uds` container (image `ghcr.io/llm-d/llm-d-uds-tokenizer:v0.7.1`), two `emptyDir` volumes (`tokenizers`, `tokenizer-uds`), and a `/tmp/tokenizer` volumeMount on the existing `epp` container so the `tokenizer` plugin can reach the UDS socket. Tracking removal of this workaround upstream — once the chart supports multiple sidecars natively, the post-renderer goes away.

</details>

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy instead of the standalone Envoy sidecar, do **not** apply the standalone chart above. Instead:

1. **Deploy a Kubernetes Gateway**. See [the gateway guides](../prereq/gateways) for step-by-step deployment of a Gateway named `llm-d-inference-gateway`.

2. **Deploy the llm-d Router and HTTPRoute** via the `inferencepool` chart with `experimentalHttpRoute.enabled=true`. Same UDS post-renderer applies:

   ```bash
   export PROVIDER_NAME=istio   # options: none, gke, agentgateway, istio
   helm install precise-prefix-cache-aware \
     oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
     -f guides/recipes/scheduler/base.values.yaml \
     -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
     --set provider.name=${PROVIDER_NAME} \
     --set experimentalHttpRoute.enabled=true \
     --set experimentalHttpRoute.inferenceGatewayName=llm-d-inference-gateway \
     --post-renderer uds-tokenizer \
     -n ${NAMESPACE} --version v1.5.0
   ```

</details>

### 3. Deploy the Model Server

Apply the Kustomize overlay for your backend (defaulting to NVIDIA GPU / vLLM):

```bash
kubectl apply -n ${NAMESPACE} -k guides/precise-prefix-cache-aware/modelserver/gpu/vllm/
```

### 4. (Optional) Enable Monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/monitoring/README.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide:

  ```bash
  kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
  ```
- Enable Prometheus scrape for the scheduler by layering `-f guides/recipes/scheduler/features/monitoring.values.yaml` onto the helm command in step 2.

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service precise-prefix-cache-aware-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
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

The benchmark launches a pod (`llmdbench-harness-launcher`) that uses `inference-perf` with a shared-prefix synthetic workload. Each experiment is saved under the specified output folder, e.g. `./results/<experiment ID>/inference-perf_<experiment ID>_precise-guide-<model name>`. See the [benchmark instructions doc](../../helpers/benchmark.md) for details.

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  ```

- [Create HuggingFace token](../../helpers/hf-token.md)

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-aware/benchmark-templates/guide.yaml"
```

### 3. Execute Benchmark

```bash
export IP=$(kubectl get service precise-prefix-cache-aware-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
envsubst < guide.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Cleanup

```bash
helm uninstall precise-prefix-cache-aware -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/precise-prefix-cache-aware/modelserver/gpu/vllm/
```

## How It Works

1. **vLLM pods publish KV-cache events** — each pod runs `vllm serve ... --kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'` with `KV_EVENTS_ENDPOINT=tcp://*:5556`, binding its own ZMQ socket. On every KV block allocation/eviction, vLLM emits a ZMQ message.
2. **Scheduler subscribes per pod** — pod-discovery (`kvEventsConfig.discoverPods: true`) wires the data-layer `endpoint-notification-source` into the scorer's `ExtractEndpoint`, so each scheduler replica installs a ZMQ subscriber per vLLM pod independently. All replicas converge to the same index.
3. **Scoring** — the `precise-prefix-cache-scorer` returns the fraction of the request's prefix blocks that are resident on each candidate pod. The `max-score-picker` routes to the highest-scoring pod.

The `tokenizer` plugin and the scorer's internal `tokenizersPoolConfig` both point at `/tmp/tokenizer/tokenizer-uds.socket` — a UDS tokenizer sidecar (`ghcr.io/llm-d/llm-d-uds-tokenizer`) owns tokenizer model downloads and caching, keeping tokenization out of the EPP main container.

## Benchmarking Report

The benchmark runs on 16× H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

<details>
<summary><b><i>Click</i></b> to view the report for <code>rate=60</code></summary>

```yaml
metrics:
  latency:
    request_latency:
      mean: 56.51
      p50:  56.20
      p90:  64.43
      p99:  72.82
      units: s
    time_to_first_token:
      mean: 0.714
      p50:  0.532
      p90:  1.534
      p99:  2.670
      units: s
    time_per_output_token:
      mean: 0.0558
      p50:  0.0555
      p90:  0.0632
      p99:  0.0720
      units: s/token
    inter_token_latency:
      mean: 0.0558
      p50:  0.0351
      p90:  0.1164
      p99:  0.2527
      units: s/token
  throughput:
    requests_per_sec:      15.52
    output_tokens_per_sec: 15132.8
    total_tokens_per_sec:  132740.7
```

</details>

### Comparing LLM-d Scheduling to a Simple Kubernetes Service

Graphs below compare the precise path to a stock Kubernetes Service that round-robins requests across the same 8 vLLM pods (no EPP, no scoring).

<img src="./benchmark-results/throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./benchmark-results/latency_vs_qps.png" width="900" alt="Latency vs QPS">

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | LLM-d Precise | Δ% vs k8s |
| :------------------ | :--------------- | :------------ | :-------- |
| Output tokens/sec   | 5,722            | 12,223        | +113.6%   |
| Requests/sec        | 35.87            | 35.89         | ≈ 0%      |
| TTFT mean (s)       | 58.10            | 0.606         | −98.96%   |
| TTFT p90 (s)        | 107.43           | 1.076         | −99.00%   |
| ITL mean (ms)       | 44.0             | 47.0          | +6.8%     |

Saturation stage `rate=60`:

| Metric              | k8s service (RR) | LLM-d Precise | Δ% vs k8s |
| :------------------ | :--------------- | :------------ | :-------- |
| Output tokens/sec   | 6,551            | 15,133        | +131.0%   |
| Requests/sec        | 60.41            | 60.90         | +0.8%     |
| TTFT mean (s)       | 75.59            | 0.714         | −99.06%   |
| TTFT p90 (s)        | 138.66           | 1.534         | −98.89%   |
| ITL mean (ms)       | 45.0             | 56.0          | +24.4%    |
