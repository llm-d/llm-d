# Tiered Prefix Cache

[![E2E (GKE GPU Native)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml)
[![E2E (GKE GPU LMcache)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml)

## Overview

This guide deploys prefix-cache offloading: evicted KV-cache blocks move from accelerator HBM to larger, more cost-effective tiers (CPU RAM, and optionally a shared filesystem) and are pulled back on demand instead of being recomputed. This increases the effective cache size and prefix-cache reuse for multi-turn and long-context workloads.

For the concepts, tier tradeoffs, and architecture, see the [Tiered Prefix Cache well-lit path](../../docs/well-lit-paths/capabilities/tiered-prefix-cache.md). This guide focuses on deployment. The prefix-aware request scheduling from the [optimized baseline](../optimized-baseline/README.md) also applies here.

## Choosing a Path

Each path is a self-contained deployment using a specific offloading implementation. Pick one and follow its deploy block under [Deploy the Model Server](#2-deploy-the-model-server).

| Path | Implementation | Tiers | Directory |
| ---- | -------------- | ----- | --------- |
| **vLLM native** | vLLM `OffloadingConnector` | CPU RAM, CPU RAM + Filesystem | `modelserver/gpu/vllm/native/` |
| **LMCache** | [LMCache](https://lmcache.ai) connector | CPU RAM, Filesystem | `modelserver/gpu/vllm/lmcache-connector/` |
| **MooncakeStore** | MooncakeStore connector | CPU RAM, Filesystem | `modelserver/gpu/vllm/mooncake-store/` |
| **SGLang HiCache** | SGLang native HiCache | CPU RAM | `modelserver/gpu/sglang/native/cpu/` |
| **TPU** | vLLM TPU KVCache connector | CPU RAM | `modelserver/tpu/v6/vllm/native/cpu/`, `modelserver/tpu/v7/vllm/native/cpu/` |

The tiers each path supports differ — see the table above. For example, the vLLM native path also extends to a shared filesystem via multi-tier offloading (`TieringOffloadingSpec`), spilling from CPU RAM to shared storage (HBM → CPU RAM → filesystem).

We recommend each model server's **native** offloading path: the `OffloadingConnector` on vLLM, and HiCache — its equivalent — on SGLang. Native offloading is low-overhead, requires no extra components, and enabling the CPU tier is appropriate in almost all deployments. Reach for a non-native connector (for example LMCache) only when you need a capability the native path does not yet provide.

## Default Configuration

### GPU

| Parameter              | Value                                                   |
| ---------------------- | ------------------------------------------------------- |
| Model                  | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| GPUs per replica (TP)  | 2                                                       |
| GPU Accelerator        | NVIDIA H100                                             |
| CPU Cache Offload Size  | 100 GB                                                 |

### TPU

| Parameter               | Value                                                   |
| ----------------------- | ------------------------------------------------------- |
| Model                   | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| TPUs per replica (TP)   | 8                                                       |
| TPU Accelerator         | TPU v7                                                  |
| HBM Staging Buffer Size | 1000 Blocks (~34 GB)                                    |
| CPU Cache Offload Size  | 25000 Chunks (~780 GB)                                  |

> [!NOTE]
> A `gpt-oss-120b` variant (TP=1 on NVIDIA H100, 100 GB CPU offload) is also benchmarked — see [gpt-oss-120B benchmarking results](./benchmark-results/vllm-gpt-oss-120b-h100.md).

---

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

* Set the following environment variables:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export NAMESPACE=llm-d-tiered-prefix-cache
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
  ```

* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.
<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

```bash
helm install tiered-prefix-cache \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. _Deploy the llm-d Router and an HTTPRoute_:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install tiered-prefix-cache \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

> [!NOTE]
> To enable tiered prefix caching, the llm-d EPP is configured with two prefix-cache scorers: one for the accelerator (GPU/TPU) cache and one for the CPU cache.
> LRU capacity for the CPU cache must be configured manually (`lruCapacityPerServer`) because vLLM does not currently emit CPU block metrics.

---

### 2. Deploy the Model Server

Deploy **one** of the paths below. Each `kubectl apply -k` targets an overlay directory. For the GPU paths, `INFRA_PROVIDER` selects a `base` overlay or a provider-specific one (for example `gke`); the TPU path does not use an infra-provider overlay.

#### vLLM native — CPU RAM

```bash
export MODEL_SERVER=vllm # vllm 
export CONNECTOR=native  # native
export VARIANT=cpu       # cpu | fs
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/native/cpu/${INFRA_PROVIDER}/
```

#### vLLM native — CPU RAM + Filesystem

This path adds a shared filesystem tier using vLLM's native multi-tier offloading. It requires a ReadWriteMany PVC mounted at `/mnt/files-storage`.

First, provision the PVC. See [Storage Backends](#storage-backends) to configure a `StorageClass` for your environment.

```bash
export STORAGE_CLASS="" # cluster default if empty; or e.g. "lustre" / "efs-sc"
envsubst < ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
```

Then deploy the model server:

```bash
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/native/fs/${INFRA_PROVIDER}/
```

#### LMCache

LMCache supports a CPU RAM tier and a filesystem tier. For the filesystem tier, first provision the PVC as shown in the [vLLM native filesystem path](#vllm-native--cpu-ram--filesystem).

```bash
export VARIANT=cpu          # cpu | fs
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/lmcache-connector/${VARIANT}/${INFRA_PROVIDER}/
```

#### SGLang HiCache — CPU RAM

```bash
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/sglang/native/cpu/${INFRA_PROVIDER}/
```

#### MooncakeStore - CPU DRAM

MooncakeStore supports two deployment modes: embedded CPU DRAM (`cpu`) and standalone CPU DRAM + SSD (`fs`). Both require the [Mooncake Master](../../helpers/mooncake-master-store/) metadata service. The `fs` variant additionally requires the [Mooncake Client](../../helpers/mooncake-client/), a standalone process that owns the CPU DRAM pool and SSD persistence tier.

**Prerequisites:**

```bash
# Required for both cpu and fs variants
kubectl apply -k ${REPO_ROOT}/helpers/mooncake-master-store/base/

# Required for fs variant only — deploys a DaemonSet that owns the CPU + SSD pool (sized for Qwen3-32B)
kubectl apply -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/mooncake-store/fs/mooncake-client/
```

**Deploy the model server:**

```bash
k apply -k ${REPO_ROOT}/helpers/mooncake-master-store/monitoring
```

After that you can deploy the modelserver manifests:

```bash
export MODEL_SERVER=vllm    # vllm 
export VARIANT=cpu          # cpu | fs
export INFRA_PROVIDER=base  # base
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/${MODEL_SERVER}/mooncake-store/${VARIANT}/${INFRA_PROVIDER}
```

#### MooncakeStore - fs (CPU DRAM + SSD offloading)

As with the CPU mooncake offloading example, start by making sure the `mooncake-master-store` is deployed (for without monitoring, use the `base` overlay, but otherwise use the `monitoring` overlay):

```bash
k apply -k ${REPO_ROOT}/helpers/mooncake-master-store/monitoring
```

Then deploy the Mooncake Client. The Client allocates CPU DRAM and SSD resources at startup and registers them with the Master, so sizing must be set before deployment — changes require a restart. This guide provides an overlay that patches the [base helper defaults](../../helpers/mooncake-client/) (40 GB DRAM, 500 GB SSD) up to values sized for Qwen3-32B (80 GB DRAM, 1 TB SSD per node):

```bash
kubectl apply -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/mooncake-store/fs/mooncake-client/
```

#### TPU (Google TPU v6 / v7)

```bash
export TPU_VERSION=v7  # v6 | v7
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/tpu/${TPU_VERSION}/vllm/native/cpu/
```

#### Storage Backends

The filesystem tier works with any storage system that exposes a ReadWriteMany PVC over standard POSIX file access. The following backends have setup guides; others (for example CephFS) work through the same PVC mechanism.

| Backend | StorageClass | Setup |
| ------- | ------------ | ----- |
| GCP Lustre (GKE) | `lustre` | [GCP Lustre guide](./manifests/backends/lustre/README.md) |
| AWS EFS | `efs-sc` | [EFS guide](./manifests/backends/aws/README.md) |

The connector does not evict data from the shared tier. Capacity is managed by the storage system or by an external controller — a reference PVC evictor is available in the [llm-d-kv-cache repository](https://github.com/llm-d/llm-d-kv-cache).

---

### 3. (Optional) Enable monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).
* Deploy the monitoring resources for model servers:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```

---

## Verification

### 1. Check the PVC (filesystem paths only)

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`.

### 2. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service tiered-prefix-cache-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 3. Send Test Requests

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

### 4. Verify KV cache is offloaded to storage (filesystem paths)

```bash
# Long prompt (~3K tokens) to trigger offload
PROMPT=$(printf 'Story: '; for i in $(seq 1 800); do printf 'alice met bob and they walked together. '; done)
jq -n --arg prompt "$PROMPT" '{"model":"Qwen/Qwen3-32B", "prompt":$prompt, "max_tokens":3, "temperature":0}' | \
curl -s http://${IP}/v1/completions \
  -H 'Content-Type: application/json' \
  -d @- | jq
```

```bash
# Check the shared PVC for written blocks
POD=$(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ${NAMESPACE} ${POD} -- du -sh /mnt/files-storage/kv-cache
kubectl exec -n ${NAMESPACE} ${POD} -- find /mnt/files-storage/kv-cache -maxdepth 5
```

Expected output: `du -sh` shows hundreds of MB to several GB, and `find` lists a path like
`/mnt/files-storage/<model>_<hash>_r0/<block-config>/<tp-config>/...` (vLLM native) or `/mnt/files-storage/kv-cache/<model>-xxx.pt` (LMCache).

If you have monitoring set up, confirm via `vllm:kv_offload_total_bytes` (vLLM native) or `lmcache:local_storage_usage` (LMCache) in the metrics explorer.

---

## Cleanup

Uninstall the router, then delete the model server overlay for the path you deployed in step 2.

```bash
helm uninstall tiered-prefix-cache -n ${NAMESPACE}
```

**GPU paths:**

```bash
export MODEL_SERVER=vllm           # vllm | sglang
export CONNECTOR=native            # native | lmcache-connector
export VARIANT=cpu                 # cpu | fs
export INFRA_PROVIDER=base         # base | gke
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/${MODEL_SERVER}/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER} --ignore-not-found
```

**TPU path:**

```bash
export TPU_VERSION=v7  # v6 | v7
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/tpu/${TPU_VERSION}/vllm/native/cpu --ignore-not-found
```

```bash
kubectl delete -f ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml -n ${NAMESPACE} --ignore-not-found  # if a PVC was created
kubectl delete namespace ${NAMESPACE}
```

If you deployed Mooncake components, clean them up as well:

```bash
kubectl delete -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/mooncake-store/fs/mooncake-client/  # if fs variant was used
kubectl delete -k ${REPO_ROOT}/helpers/mooncake-master-store/base/
```

---

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a shared-prefix synthetic workload designed to exercise tiered cache eviction behavior against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **tiered-prefix-cache-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to exercise tiered cache eviction across HBM and CPU RAM — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

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
export ENDPOINT_URL="http://$(kubectl get service tiered-prefix-cache-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
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

### 3. Run the benchmark profile for Tiered Prefix Cache

`guide_tiered-prefix-cache_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the load profile used to generate the [results below](#benchmarking-report) (250 prefix groups × 5 prompts each on a 60-second Poisson interval) and is shaped to exercise eviction across HBM and CPU RAM.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/tiered-prefix-cache \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "Qwen/Qwen3-32B" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_tiered-prefix-cache_1.yaml \
    --analyze
```

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.

---

## Benchmarking Reports

Empirical benchmark reports demonstrating the impact of multi-tier prefix-cache offloading relative to HBM-only serving configurations under high-cache workloads:

- **[Qwen/Qwen3-32B on vLLM (16×H100 CPU Offload)](./benchmark-results/vllm-qwen3-32b-h100.md)**: Headline throughput and latency comparisons across 16×H100 GPUs with CPU RAM offloading.
- **[Qwen/Qwen3-32B on SGLang (16×H100 CPU Offload)](./benchmark-results/sglang-qwen3-32b-h100.md)**: Headline throughput and latency comparisons across 16×H100 GPUs with SGLang HiCache CPU RAM offloading.
- **[openai/gpt-oss-120b on vLLM (16×H100 CPU Offload)](./benchmark-results/vllm-gpt-oss-120b-h100.md)**: Stage-by-stage throughput, latency, TPOT, and fleet cache hit rate breakdowns across 5–40 QPS.
- **[Qwen/Qwen3-32B on vLLM (TPU v6e/v7 CPU Offload)](./benchmark-results/vllm-qwen3-32b-tpuv7.md)**: Headline throughput and latency effect of CPU RAM prefix offloading on Google TPU architectures.
- **[Qwen/Qwen3-32B on vLLM (16×H100 Lustre Offload)](./benchmark-results/vllm-qwen3-32b-h100-lustre.md)**: Benchmark comparisons for shared POSIX filesystem offloading using LMCache and llm-d filesystem connectors.

For detailed results see [gpt-oss-120B benchmarking results](benchmark-results-gpt-oss-120b.md).
