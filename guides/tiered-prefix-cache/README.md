# Tiered Prefix Cache

[![E2E (GKE GPU Native)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-native.yaml)
[![E2E (GKE GPU SGLang)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-sglang-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-sglang-native.yaml)
[![E2E (GKE GPU LMcache)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-gpu-vllm-lmcache.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-gke-cpu-tpu-vllm-native.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-ibm-cpu-gpu-vllm-native.yaml)
[![E2E (Intel XPU Native)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-intel-acc-xpu-vllm-native.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-intel-acc-xpu-vllm-native.yaml)
[![E2E (Intel XPU LMcache)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-intel-acc-xpu-vllm-lmcache.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-tiered-prefix-cache-intel-acc-xpu-vllm-lmcache.yaml)

## Overview

This guide extends each model server's prefix cache beyond accelerator HBM: instead of discarding evicted KV blocks, the model servers offload them to CPU RAM and, optionally, a shared filesystem, and load them back when a follow-on request (a multi-turn or agentic conversation) reuses the prefix, rather than recomputing its prefill.

It builds on the [Optimized Baseline](../optimized-baseline/README.md): the router keeps the same prefix- and load-aware scoring and adds a second prefix-cache scorer that tracks what each model server holds in its CPU tier, so a request also prefers the pod that can load its prefix from CPU RAM.

The default deployment serves `Qwen/Qwen3-32B` on NVIDIA GPUs with two replicas at tensor parallelism 2, each offloading to CPU RAM through vLLM's native `OffloadingConnector` (`CONNECTOR=native`, `VARIANT=cpu`). The offloading implementation is selected with `CONNECTOR` and the tier with `VARIANT` (`cpu`: HBM → CPU RAM; `fs`: HBM → CPU RAM → a shared ReadWriteMany filesystem, see [Filesystem tier](#filesystem-tier)):

| `CONNECTOR` | Implementation | `VARIANT` | Model servers and accelerators |
| --- | --- | --- | --- |
| `native` (recommended) | vLLM [`OffloadingConnector`](https://vllm-project.github.io/2026/01/08/kv-offloading-connector.html) (`fs` adds a filesystem tier through `TieringOffloadingSpec`); SGLang HiCache; the vLLM TPU offload connector | `cpu`, `fs` (TPU: `cpu`) | vLLM on NVIDIA GPU, AMD GPU, Intel XPU, Google TPU; SGLang on NVIDIA GPU |
| `lmcache-connector` | [LMCache](https://lmcache.ai) | `cpu`, `fs` | vLLM on NVIDIA GPU, AMD GPU, Intel XPU |
| `mooncake-store` | MooncakeStore (`cpu`: embedded CPU DRAM pool; `fs`: standalone [Mooncake Client](../../helpers/mooncake-client/) that owns a CPU DRAM + SSD pool) | `cpu`, `fs` | vLLM on NVIDIA GPU |

Use `native` unless you need a capability it does not provide yet. The vLLM native overlays give each replica a 100 GB CPU tier (`cpu_bytes_to_use`), SGLang uses `--hicache-size=200`, LMCache uses `LMCACHE_MAX_LOCAL_CPU_SIZE`, and the TPU connector uses `TPU_OFFLOAD_NUM_CPU_CHUNKS`. Keep the CPU tier within the pod's memory limit when you change it. Serving a model whose attention layers use different head dimensions (e.g. Gemma 4) with the vLLM native connector needs one extra flag; see [KV-Cache Offloading](../../docs/architecture/advanced/kv-management/kv-offloader.md#vllm-native-cpu-offloading).

For why offloading helps, the storage tiers and when to add each, and how the CPU and filesystem tiers work, see [KV-Cache Offloading](../../docs/architecture/advanced/kv-management/kv-offloader.md).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves one model:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `Qwen/Qwen3-32B` | ✅ validated | ✅ validated | Default. H100 80 GB reference · 2 replicas × TP=2 (4 GPUs) · `INFRA_PROVIDER`: `base`, `gke` (`mooncake-store`: `base`) · SGLang: `CONNECTOR=native` (HiCache) |
| AMD GPU | `amd` | `Qwen/Qwen3-32B` | ✅ validated | — | 2 replicas × TP=2 (4 GPUs) · `CONNECTOR`: `native`, `lmcache-connector` · `INFRA_PROVIDER`: `base`, `amd-ci` |
| Intel XPU | `xpu` | `Qwen/Qwen3-32B` | ✅ validated | — | Intel B60 · 1 replica × TP=4 via DRA · `CONNECTOR`: `native`, `lmcache-connector` (the `lmcache-connector` overlay serves `Qwen/Qwen3-8B` on 1 XPU) |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | ✅ validated | — | GKE only · 3 replicas × 8 chips (`2x4`, TP=8) · `CONNECTOR=native`, `VARIANT=cpu` |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3-32B` | 🟡 community | — | GKE only · 3 replicas × 4 chips (`2x2x1`, TP=8) · `CONNECTOR=native`, `VARIANT=cpu` · multi-host variant (`HOST_TYPE=multi-host`) serves `Qwen/Qwen3-Coder-480B-A35B-Instruct` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 2 replicas with tensor parallelism 2, 4 GPUs in total) and enough host memory for the CPU tier. If your cluster has fewer resources, adjust `replicas` and `--tensor-parallel-size` in the [model server patch](./modelserver/gpu/vllm/base/patch-vllm.yaml) for your environment.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- For `VARIANT=fs`, a storage system that provides ReadWriteMany PVCs (see [Filesystem tier](#filesystem-tier)). For `HOST_TYPE=multi-host` on TPU v7, the [LeaderWorkerSet (LWS) controller](../../docs/infrastructure/multi-node.md).

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
export GUIDE_NAME=tiered-prefix-cache
export NAMESPACE=llm-d-tiered-prefix-cache
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v6, tpu/v7
export MODEL_SERVER=vllm # options: vllm, sglang
export CONNECTOR=native # options: native, lmcache-connector, mooncake-store; offloading implementation (table above)
export VARIANT=cpu # options: cpu, fs; offload tier: cpu = CPU RAM, fs = CPU RAM + shared filesystem
export INFRA_PROVIDER=base # options: base, gke, amd-ci
export HOST_TYPE=single-host # options: single-host, multi-host; multi-host: ACCELERATOR_TYPE=tpu/v7 only
export STORAGE_CLASS= # VARIANT=fs only: StorageClass of the shared PVC, empty for the cluster default
export MODEL=Qwen/Qwen3-32B # set to the model your accelerator serves (table above); xpu + lmcache-connector: Qwen/Qwen3-8B, tpu/v7 multi-host: Qwen/Qwen3-Coder-480B-A35B-Instruct
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

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below):

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${HOST_TYPE}/${GUIDE_NAME}-cpu.values.yaml"
```
<!-- guide:deploy.router_values end -->

> [!NOTE]
> The router values configure two prefix-cache scorers: one for the accelerator (HBM) cache and one for the CPU tier. vLLM does not report how full its CPU tier is, so the CPU producer's capacity is set by hand (`lruCapacityPerServer`): the `single-host` values are sized for Qwen3-32B and the `multi-host` values for Qwen3-Coder-480B. Recompute it for other models.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

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

#### Filesystem tier

For `VARIANT=fs` with the `native` or `lmcache-connector` connector, **provision the shared PVC** first (`llm-d-kv-cache-storage`, mounted at `/mnt/files-storage`). The `amd-ci` overlays ship their own PVC, and MooncakeStore persists to the Mooncake Client's SSD pool instead:

<!-- guide:deploy.storage start -->
```bash
# only when VARIANT=fs and CONNECTOR=native or lmcache-connector and INFRA_PROVIDER=base or gke:
# ReadWriteMany PVC for the filesystem tier, mounted at /mnt/files-storage
envsubst < ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
```
<!-- guide:deploy.storage end -->

The filesystem tier works with any storage system that exposes a ReadWriteMany PVC over standard POSIX file access. The following backends have setup guides; others (for example CephFS) work through the same PVC mechanism:

| Backend | `STORAGE_CLASS` | Setup |
| ------- | --------------- | ----- |
| GCP Lustre (GKE) | `lustre` | [GCP Lustre guide](./manifests/backends/lustre/README.md) |
| AWS EFS | `efs-sc` | [EFS guide](./manifests/backends/aws/README.md) |

The connectors do not evict data from the shared tier. Capacity is managed by the storage system or by an external controller; a reference PVC evictor is available in the [llm-d-kv-cache repository](https://github.com/llm-d/llm-d-kv-cache).

#### MooncakeStore

For `CONNECTOR=mooncake-store`, **deploy the [Mooncake Master](../../helpers/mooncake-master-store/) metadata service**, and for `VARIANT=fs` the Mooncake Client. The Client allocates its CPU DRAM and SSD pool at startup and registers it with the Master, so sizing is set before deployment (this overlay raises the [helper defaults](../../helpers/mooncake-client/) of 40 GB DRAM and 500 GB SSD to 80 GB DRAM and 1 TB SSD per node):

> [!NOTE]
> Both variants use RDMA. The model server pods (and the Mooncake Client for `fs`) request `rdma/ib: 1`, so nodes must expose that resource or the pods stay `Pending`. See [RDMA and Networking Configuration](../../docs/infrastructure/rdma/README.md).

<!-- guide:deploy.mooncake start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when CONNECTOR=mooncake-store:
# Mooncake Master metadata service (namespace mooncake); use .../monitoring to also scrape it
kubectl apply -k ${REPO_ROOT}/helpers/mooncake-master-store/base/

# only when CONNECTOR=mooncake-store and VARIANT=fs:
# Mooncake Client DaemonSet that owns the CPU DRAM + SSD pool, sized for Qwen3-32B
kubectl apply -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/mooncake-store/fs/mooncake-client/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.mooncake end -->

#### Model servers

**Apply the Kustomize overlay** for your accelerator, model server, connector, and tier (`INFRA_PROVIDER=gke` applies to NVIDIA GPU and TPU on GKE, `amd-ci` to the AMD CI cluster; use `base` elsewhere). With `HOST_TYPE=multi-host`, TPU v7 runs `Qwen/Qwen3-Coder-480B-A35B-Instruct` across hosts with a LeaderWorkerSet instead; deploy the router with the same `HOST_TYPE` so it routes only to the leader pods:

<!-- guide:deploy.modelserver start -->
```bash
# only when HOST_TYPE=single-host:
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip start -->
```bash
# only when HOST_TYPE=multi-host:
# TPU v7 multi-host (LeaderWorkerSet), serving Qwen/Qwen3-Coder-480B-A35B-Instruct
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/tpu/v7/vllm/native/cpu/multi-host/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver end -->

The ROCm native overlays raise vLLM's Triton swap threshold so CPU-to-GPU loads use the fast path; see [ROCm vLLM native offloading](./modelserver/amd/vllm/native/README.md).

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

### 3. Observability & Troubleshooting

Once monitoring is enabled, use the signals below to operate tiered prefix caching. Metric definitions are in the [metric reference](../../docs/operations/observability/model-server-metrics.md#vllm-kv-offloading-metrics) and queries are in the [PromQL reference](../../docs/operations/observability/promql.md#tiered-prefix-cache).

Offloading only pays off when blocks evicted from HBM are loaded back later instead of recomputed. Two views need to agree: the router keeps a separate prefix index per tier (`gpu-prefix-cache-producer` and `cpu-prefix-cache-producer` in this guide's router values), and the model server reports what it actually stored and loaded. Most problems show up as a gap between the two.

#### Key metrics for this path

| Signal | Why it matters for tiered prefix cache |
| ------ | -------------------------------------- |
| Offload tier hit rate (`vllm:external_prefix_cache_hits_total` / `vllm:external_prefix_cache_queries_total`) | Prompt tokens found in CPU RAM or the filesystem tier at scheduling time, so they are loaded instead of recomputed. Read it next to the HBM hit rate (`vllm:prefix_cache_hits_total`): under cache pressure the HBM rate drops and this one should pick up the difference |
| Store and load volume (`vllm:kv_offload_store_bytes_total`, `vllm:kv_offload_load_bytes_total`) | Steady stores with almost no loads means evicted blocks are written but never reused, so the tier costs memory and copy time without saving any prefill |
| Load speed (`vllm:kv_offload_load_bytes_total` / `vllm:kv_offload_load_time_total`) | A load has to finish faster than the prefill it replaces. This covers the copy from CPU RAM to the accelerator only; on the filesystem path, reading blocks from storage into CPU RAM happens first and is not included |
| Store allocation failures (`vllm:kv_offload_allocation_failure_total`) | Stores that could not get CPU blocks because too little of the CPU tier was free or evictable. Those chunks are not offloaded, and the model server logs `cannot store chunks` |
| Router prefix index by tier (`llm_d_epp_prefix_indexer_size`, `llm_d_epp_prefix_indexer_hit_ratio`, split by `plugin_name`) | What the router believes each tier holds. The size gauge is the total across pods, so a full CPU index reads about pods × `lruCapacityPerServer` |
| TTFT (`vllm:time_to_first_token_seconds`) | The user-facing payoff of this path. Compare against an HBM-only run at the same load before attributing a change to offloading |

vLLM does not export how full the CPU tier is, which is why `lruCapacityPerServer` on the CPU producer is set by hand; `vllm:kv_offload_cpu_cache_usage_perc` is the share of the CPU tier pinned by in-flight transfers, not its occupancy. SGLang HiCache does export occupancy (`sglang_hicache_host_used_tokens` / `sglang_hicache_host_total_tokens`) and attributes hits by tier with `sglang_cached_tokens_total{cache_source="host"}`. LMCache reports `lmcache:retrieve_hit_rate` and `lmcache:local_cache_usage`.

#### Common failure modes

- **Offload tier hit rate near zero while the HBM hit rate falls**: blocks are not reaching the offload tier, or they are evicted from it before reuse. If `vllm:kv_offload_store_bytes_total` is flat, confirm the pod started with the connector in `--kv-transfer-config`. If stores are steady but loads stay near zero, either the workload rarely repeats prefixes or the CPU tier is too small for the working set; for the second case, raise `cpu_bytes_to_use`, keeping it within the pod's memory limit.
- **Router CPU index hit ratio well above what the model server reports**: the CPU index records the same prefixes as the HBM index with a larger capacity, so its hit ratio should roughly track HBM plus offload hits. If it runs well above that, the router is steering requests toward blocks the model servers no longer hold. The likely cause is `lruCapacityPerServer` on `cpu-prefix-cache-producer` being larger than what `cpu_bytes_to_use` actually fits, so lower it.
- **TTFT worse than the HBM-only baseline**: loads are slower than recomputing. Check load speed, and on filesystem paths check read throughput on the storage backend.
- **Steady store allocation failures**: the CPU tier has no free or evictable blocks when a store is prepared, usually because too many blocks are held by in-flight transfers under high concurrency. `vllm:kv_offload_cpu_cache_usage_perc` staying near 1.0 confirms it. Increase `cpu_bytes_to_use`.

The bundled [alerting rules](../../docs/operations/observability/alerting.md) do not cover offloading yet.

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

For `VARIANT=fs`, **check that the shared PVC is `Bound`** first:

<!-- guide:verify.tests.pvc start -->
```bash
# only when VARIANT=fs and CONNECTOR=native or lmcache-connector:
kubectl get pvc llm-d-kv-cache-storage -n ${NAMESPACE}
```
<!-- guide:verify.tests.pvc end -->

**Check that `MODEL` matches the model the servers load** (the step fails and prints the right value if it does not):

<!-- guide:verify.tests.model start -->
```bash
# Fail fast if MODEL does not match the model the servers load (two
# sub-variants serve a different model; see the MODEL comment above).
# `false` (not `exit 1`) fails the step without closing an interactive shell.
POD=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} \
    -o go-template='{{range .items}}{{$i := index .metadata.labels "leaderworkerset.sigs.k8s.io/worker-index"}}{{if or (not $i) (eq $i "0")}}{{.metadata.name}} {{end}}{{end}}' | awk '{print $1}')
SERVED=$(kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${POD}:8000/proxy/v1/models" \
  | grep -o '"id": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
echo "model servers serve: ${SERVED}"
[ "${SERVED}" = "${MODEL}" ] \
  || { echo "MODEL=${MODEL} does not match the served model: export MODEL=${SERVED}" >&2; false; }
```
<!-- guide:verify.tests.model end -->

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

### 3. Verify KV-cache offloading

A served request only proves the stack is up. To confirm that the model servers write KV-cache blocks to the offload tier, send a burst of requests that share a long prompt prefix and read the offload counters of every model server pod.

**Send 10 requests that share a ~2k-token prefix:**

<!-- guide:verify.tests.shared_prefix start -->
```bash
# 10 requests that share a ~2k-token prefix and differ only in the last sentence
kubectl run prefix-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 100); do printf "Evicted KV-cache blocks move to CPU RAM and are loaded back instead of recomputed. "; done)
    for i in $(seq 1 10); do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question ${i}: what is llm-d?\", \"max_tokens\": 16}"
    done'
```
<!-- guide:verify.tests.shared_prefix end -->

**Read the offload counters** of every model server pod. What to expect on the pod that served the burst:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

<!-- guide:verify.tests.offload_metrics[0] start -->
```bash
# Offload counters of every model server pod, read through the
# Kubernetes API server proxy (no port-forward needed). On TPU
# multi-host only LeaderWorkerSet leaders (worker-index 0) serve :8000.
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} \
    -o go-template='{{range .items}}{{$i := index .metadata.labels "leaderworkerset.sigs.k8s.io/worker-index"}}{{if or (not $i) (eq $i "0")}}{{.metadata.name}} {{end}}{{end}}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^(vllm:(kv_offload_(store|load)_bytes_total|external_prefix_cache_(hits|queries)_total)|lmcache:(local_cache_usage|retrieve_hit_rate))' || true
done
```
<!-- guide:verify.tests.offload_metrics[0] end -->

With `CONNECTOR=native`, `vllm:kv_offload_store_bytes_total` is above zero (the prefix blocks were written to the CPU tier) and `vllm:external_prefix_cache_queries_total` counts the prompt tokens looked up in the offload tier. On the TPU, LMCache, and MooncakeStore connectors, look at `vllm:external_prefix_cache_queries_total` (and `lmcache:local_cache_usage` for LMCache).

For `CONNECTOR=native` on GPU, AMD, and Intel XPU, **assert that blocks were offloaded**; the step fails if the stored bytes are still zero:

<!-- guide:verify.tests.offload_gate start -->
```bash
# only when MODEL_SERVER=vllm and CONNECTOR=native and ACCELERATOR_TYPE=gpu or amd or xpu:
# Gate: the native OffloadingConnector writes every computed block to
# the offload tier, so after the burst above the bytes stored, summed
# over all model server pods, must be above zero.
STORED=0
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  STORED=$(kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | awk -v s="${STORED}" '/^vllm:kv_offload_store_bytes_total/ {s += $NF} END {print s + 0}')
done
echo "vllm:kv_offload_store_bytes_total (all pods): ${STORED}"
awk -v s="${STORED}" 'BEGIN {exit !(s > 0)}' \
  || { echo "no KV-cache blocks were written to the offload tier: the connector is not active" >&2; false; }
```
<!-- guide:verify.tests.offload_gate end -->

</details>
<details>
<summary><b>SGLang</b></summary>

<!-- guide:verify.tests.offload_metrics[1] start -->
<!-- llm-d-cicd:skip start -->
```bash
# HiCache counters of every model server pod, read through the
# Kubernetes API server proxy (no port-forward needed). On TPU
# multi-host only LeaderWorkerSet leaders (worker-index 0) serve :8000.
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} \
    -o go-template='{{range .items}}{{$i := index .metadata.labels "leaderworkerset.sigs.k8s.io/worker-index"}}{{if or (not $i) (eq $i "0")}}{{.metadata.name}} {{end}}{{end}}'); do
  echo "== ${pod}"
  kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:8000/proxy/metrics" \
    | grep -E '^sglang[:_](hicache_host_used_tokens|evicted_tokens_total|load_back_tokens_total|cached_tokens_total)' || true
done
```
<!-- llm-d-cicd:skip end -->
<!-- guide:verify.tests.offload_metrics[1] end -->

`sglang_hicache_host_used_tokens` is above zero (the `write_through` policy copies every computed block to host memory).

</details>
<!-- tabs:end -->

Hits from the offload tier (`vllm:external_prefix_cache_hits_total`, `vllm:kv_offload_load_bytes_total`, or `sglang_cached_tokens_total{cache_source="host"}` and `sglang_load_back_tokens_total`) appear once a prefix has been evicted from HBM and is requested again, which happens under real cache pressure rather than with 10 requests.

For vLLM `native` with `VARIANT=fs` and at least two model server pods (Intel XPU runs one, so the check is skipped there), the shared tier makes a hit easy to provoke: **send a new prefix to one pod, then to a second pod** that has never seen it. The second pod loads the blocks the first one wrote to the filesystem, so its `vllm:external_prefix_cache_hits_total` is above zero:

<!-- guide:verify.tests.shared_tier start -->
```bash
# only when MODEL_SERVER=vllm and VARIANT=fs and CONNECTOR=native:
# Prime a new prefix on the first pod, then send it to the second pod,
# which has never seen it and must load it from the shared filesystem tier
PODS=($(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'))
IPS=($(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].status.podIP}'))
if [ ${#PODS[@]} -lt 2 ]; then
  echo "needs at least 2 model server pods, found ${#PODS[@]} (e.g. Intel XPU runs 1): skipping"
else
kubectl run shared-tier-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="FIRST=${IPS[0]}" \
  --env="SECOND=${IPS[1]}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 100); do printf "The shared filesystem tier lets every replica reuse KV-cache blocks written by another. "; done)
    for ip in ${FIRST} ${SECOND}; do
      curl -sS -o /dev/null -w "${ip}: HTTP %{http_code}\n" -X POST "http://${ip}:8000/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P}\", \"max_tokens\": 1}"
      sleep 10
    done'
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${PODS[1]}:8000/proxy/metrics" \
  | grep -E '^vllm:external_prefix_cache_hits_total' || true
fi
```
<!-- guide:verify.tests.shared_tier end -->

If the store counters stay at zero, the connector is not active: check that the pod started with the expected `--kv-transfer-config` (or the `--hicache-*` flags on SGLang) and see [Common failure modes](#common-failure-modes). Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
# only when HOST_TYPE=single-host:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER}
```
<!-- llm-d-cicd:skip start -->
```bash
# only when HOST_TYPE=multi-host:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/tpu/v7/vllm/native/cpu/multi-host
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.storage start -->
```bash
# only when VARIANT=fs and CONNECTOR=native or lmcache-connector and INFRA_PROVIDER=base or gke:
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/pvc.yaml --ignore-not-found=true
```
<!-- guide:cleanup.storage end -->

<!-- guide:cleanup.mooncake start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when CONNECTOR=mooncake-store and VARIANT=fs:
kubectl delete -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/mooncake-store/fs/mooncake-client/ --ignore-not-found=true

# only when CONNECTOR=mooncake-store:
kubectl delete -k ${REPO_ROOT}/helpers/mooncake-master-store/base/ --ignore-not-found=true
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.mooncake end -->

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
