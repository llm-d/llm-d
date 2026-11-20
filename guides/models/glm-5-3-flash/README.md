# GLM-5.3-Flash-Quark-MXFP4 on AMD MI355X

## Overview

This guide deploys [GLM-5.3-Flash-Quark-MXFP4](https://huggingface.co/amd/GLM-5.3-Flash-Quark-MXFP4)
on two AMD Instinct MI355X nodes, prefill/decode disaggregated into one prefill and one decode
engine (1P1D) managed as a single `DisaggregatedSet`. It targets agentic, multi-turn traffic,
where a session returns to a long prefix after every tool call. The configuration layers:

- **P/D disaggregation** with MoRI-IO KV transfer over RDMA (READ mode), so prefill never
  stalls decode.
- **CPU KV offloading** on prefill (native vLLM `OffloadingConnector`): evicted session prefixes
  stay reusable after HBM fills, so a resumed turn reloads its context instead of recomputing it.
- **Hybrid-model support end to end.** GLM-5.3-Flash has 34 KDA (linear attention) layers and 11
  DSA layers (MLA with a sparse indexer and a short tail ring), MXFP4 (Quark) experts and a bf16
  KV cache. Prefix caching, the CPU tier and the MoRI-IO transfer all carry the MLA and indexer
  KV, the KDA state and the DSA tail on vLLM v0.34.0.

The guide composes the [P/D disaggregation](../../pd-disaggregation/README.md) and
[tiered prefix cache](../../tiered-prefix-cache/README.md) foundations. These manifests were
tested on the llm-d AMD CI cluster (MI355X nodes with 8 Pollara AI NICs each); the `base` overlay
needs your cluster's NIC attachments (see [Prerequisites](#prerequisites)).

> 🚧 Community-maintained, not covered by nightly E2E. The routing sidecar uses a development
> image (`nightly`) for the MoRI-IO flags. Pin it to a release before relying on this in production.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [amd/GLM-5.3-Flash-Quark-MXFP4](https://huggingface.co/amd/GLM-5.3-Flash-Quark-MXFP4) (MXFP4 experts, bf16 attention and KV; 34 KDA + 11 DSA layers) |
| Accelerator | AMD Instinct MI355X (gfx950), 2 nodes × 8 GPUs |
| Serving topology | P/D disaggregated: 1 prefill + 1 decode, each TP=8 with expert parallelism on one node. The TP must match on both roles so MoRI-IO can split the KDA state |
| Image | `vllm/vllm-openai-rocm:v0.34.0`, pinned by digest: the last release that loads the Quark MXFP4 checkpoint. vLLM main fails since [vllm#66118](https://github.com/vllm-project/vllm/pull/66118) (fix: [vllm#68197](https://github.com/vllm-project/vllm/pull/68197)) |
| KV transfer | `MoRIIOConnector` in READ mode (`read_mode: "true"`): prefill is the producer, decode pulls the KV. WRITE mode rejects this model's DSA tail ring, so the routing sidecar runs without `--moriio-write-mode` (serial prefill-then-decode) and with `--moriio-tp-size=8`. Both roles set `VLLM_SSM_CONV_STATE_LAYOUT=DS`: MoRI-IO moves the KDA conv state only in the dim-first layout |
| Prefix caching | `--enable-prefix-caching`: vLLM keeps the KDA state in `align` mode and raises the KV block to 640 tokens at TP=8, so prefixes are reused and offloaded in 640-token blocks |
| HBM for KV | `--kv-cache-memory-bytes` = 30 GB per GPU (`KV_CACHE_MEMORY_BYTES`), about 2.5M tokens per engine. Kept explicit because Pollara AI NICs cannot register more than 2 GB of contiguous memory; a smaller HBM tier also makes the CPU tier useful at lower load |
| KV cache offload | Prefill `MultiConnector` = [`OffloadingConnector` (`cpu_bytes_to_use` = 512 GiB per engine), `MoRIIOConnector` producer]. Offloads all KV groups, including the KDA state. On/off with the `offloading-cpu` component; see [CPU KV Offloading](#cpu-kv-offloading) |
| Context and batch | `--max-model-len 262144`, `--max-num-seqs 64`: agentic coding sessions reach 100K-250K tokens. The model supports 1M positions; AMD's model card documents 64K ([Known Limitations](#known-limitations)) |
| Kernels | `--attention-backend ROCM_AITER_MLA_SPARSE`, `--sparse-indexer-topk-backend aiter`, `VLLM_ROCM_USE_AITER=1`, `VLLM_ROCM_USE_AITER_FUSION_SHARED_EXPERTS=1`, `VLLM_ROCM_USE_AITER_TRITON_SPARSE_MLA=1`, `--tokenizer-mode glm5_next`: the ROCm path of the vLLM GLM-5.3-Flash recipe, which uses the AITER top-k kernel on MI355X for traffic of 64K tokens and more |
| Parsers | `--tool-call-parser glm47`, `--reasoning-parser glm47`, `--enable-auto-tool-choice` |
| Router | P/D plugin chain; approximate prefix index on 640-token blocks sized for the GPU + CPU tiers (`lruCapacityPerServer: 12600`), in [`router/glm-5-3-flash.values.yaml`](router/glm-5-3-flash.values.yaml) |

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combination:

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| AMD Instinct MI355X | `amd` | `amd/GLM-5.3-Flash-Quark-MXFP4` | 🟡 community | 2 nodes / 16 GPUs: 1P (TP=8 + EP) + 1D (TP=8 + EP) · MoRI-IO KV transfer (READ mode) over RDMA · native CPU KV offloading on prefill (512 GiB per engine) · vLLM v0.34.0 · `INFRA_PROVIDER`: `base`, `amd-ci` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.
- Two AMD Instinct MI355X (gfx950) nodes with 8 GPUs each, exposed as `amd.com/gpu` by the AMD GPU operator.
- RDMA NICs that MoRI-IO can use between the two nodes, one per GPU. The `base` overlay does not
  attach secondary networks; add your cluster's NIC attachments and resources to it. The `amd-ci`
  overlay is a worked example for AMD Pollara AI NICs (`amd-host-device-nad` Multus attachments
  `net1`..`net8`, `amd.com/vnic: 8`, MoRI fabric settings) in
  [`components/amd-ci`](modelserver/amd/vllm/components/amd-ci/kustomization.yaml).
- Host memory: the prefill pod requests 896 GiB (384 GiB for the engine plus the 512 GiB pinned CPU
  KV pool), the decode pod 384 GiB. The amdgpu driver pins at most its TTM page limit, half of host
  RAM by default (`/sys/module/ttm/parameters/pages_limit`).
- [LeaderWorkerSet](https://lws.sigs.k8s.io/) with the `DisaggregatedSet` controller (installed below).
- `INFRA_PROVIDER=amd-ci` only: a ReadWriteMany PVC `glm53-flash-mxfp4-model-pvc` in the target
  namespace that holds the checkpoint in Hugging Face cache layout
  (`hub/models--amd--GLM-5.3-Flash-Quark-MXFP4/snapshots/b5688f25491202978c19c4d036eef579f61bbe07`).
  The engines run with `HF_HUB_OFFLINE=1`.
- The checkpoint is public, so a [HuggingFace token](../../../helpers/hf-token.md) is optional
  (it lifts Hub rate limits for the `base` overlay, which downloads about 172 GiB per pod).
- (Optional) Install the [monitoring stack](../../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=glm-5-3-flash
export NAMESPACE=llm-d-glm-5-3-flash
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_TYPE=amd # options: amd
export MODEL_SERVER=vllm # options: vllm
export INFRA_PROVIDER=base # options: base, amd-ci
export MODEL=amd/GLM-5.3-Flash-Quark-MXFP4 # set to the model your accelerator serves (table above)
```
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

Install LeaderWorkerSet with the `DisaggregatedSet` controller:

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
  --set enableDisaggregatedSet=true \
  --wait --timeout 300s
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.lws end -->

Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

(Optional) Create the `llm-d-hf-token` secret in your target namespace:

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

Prepare the paths to the `helm` values files for the `llm-d` router:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files: the base router values, then the GLM-5.3-Flash
# P/D plugin chain with the GPU+CPU prefix index on top
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

(Optional) Enable Prometheus monitoring on the `llm-d` router:

<!-- guide:deploy.monitoring_values start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

Deploy the router in [Standalone Mode](../../../docs/architecture/router/proxy.md):

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

Apply the overlay for your cluster (`INFRA_PROVIDER`). Both overlays deploy 1P1D with CPU KV
offloading on prefill:

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver end -->

The engines take about 13 minutes to start on a cold node: the weights load in about 90 s from a
local cache, the rest is AITER kernel compilation. The first request also compiles kernels for its
shape.

(Optional) Deploy the monitoring resources for the model servers:

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
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
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"Explain how a simple agent loop works in 3 sentences.\", \"max_tokens\": 128}"'
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

**Send the same long prompt three times through the P/D path:**

<!-- guide:verify.tests.pd_requests start -->
```bash
# The same ~1.7K-token prompt 3 times: each request is prefilled on the
# prefill pod and its KV cache pulled by the decode pod over MoRI-IO;
# the repeats hit the prefix cache
kubectl run pd-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 100); do printf "The prefill pod computes the KV cache and the decode pod pulls it to generate tokens. "; done)
    for i in 1 2 3; do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question: what is llm-d?\", \"max_tokens\": 16}"
    done'
```
<!-- guide:verify.tests.pd_requests end -->

### 3. Check the KV Transfer and the CPU Tier

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Request, KV-transfer and CPU-offload counters of both roles through the API
# server proxy (prefill serves on 8000, decode on 8200 behind the sidecar).
for role in prefill decode; do
  port=8000; [ "${role}" = decode ] && port=8200
  for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=${role} -o jsonpath='{.items[*].metadata.name}'); do
    echo "== ${role}: ${pod}"
    kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:${port}/proxy/metrics" \
      | grep -E '^vllm:(request_success_total|prompt_tokens_total|prefix_cache_hits_total|external_prefix_cache_hits_total|prompt_tokens_cached_by_source_total|kv_offload_(store|load)_bytes_total)' || true
  done
done
```
<!-- guide:verify.tests.pod_metrics end -->

On the decode pod, `vllm:external_prefix_cache_hits_total` grows with every request: those are the
tokens pulled from prefill over MoRI-IO. On the prefill pod, `vllm:kv_offload_store_bytes_total`
grows as computed blocks are written to the CPU tier, and `vllm:prefix_cache_hits_total` counts the
repeats served from HBM. `vllm:kv_offload_load_bytes_total` and
`vllm:prompt_tokens_cached_by_source_total{source="host"}` only grow once a prefix has been evicted
from HBM and comes back, which takes more traffic than this test (about 2.5M tokens of distinct
prompts at the default HBM size).

## CPU KV Offloading

The prefill engine runs `MultiConnector` with the `OffloadingConnector` first and the MoRI-IO
producer second. Every block the prefill engine computes is also written to a pinned CPU pool
(write-through). When a later request shares a prefix that HBM has already evicted, prefill loads
those blocks from CPU instead of recomputing them, then decode pulls the full KV over MoRI-IO as
usual. Decode does not offload.

The CPU tier only helps when it holds more than HBM does, because write-through makes the pool
first duplicate HBM. The defaults give about 2.5M tokens of HBM KV (30 GB per GPU) and about 5.6M
tokens of CPU KV (512 GiB per engine at about 98 KB per token: MLA latent, indexer keys and KDA state).

| Knob | Where | Default |
| --- | --- | --- |
| On/off | [`components/offloading-cpu`](modelserver/amd/vllm/components/offloading-cpu/kustomization.yaml) sets `OFFLOADING_MODE=cpu` on prefill; leave the component out for MoRI-IO only | on (`base`, `amd-ci`) |
| CPU pool per engine | `KV_OFFLOAD_CPU_BYTES` (prefill) | 549755813888 (512 GiB), split across the 8 ranks |
| Prefill memory | `resources` of the prefill container in the same component | 896Gi = 384Gi + the pool |
| HBM for KV per GPU | `KV_CACHE_MEMORY_BYTES` (both roles) | 30000000000 |
| Router prefix index | `lruCapacityPerServer` in [`router/glm-5-3-flash.values.yaml`](router/glm-5-3-flash.values.yaml) | 12600 blocks of 640 tokens (GPU + CPU); 3850 without offloading |

On ROCm the pool is a pinned allocation per rank, not a `/dev/shm` region, so it counts toward the
pod memory limit but not the `/dev/shm` size. Change the pool, the memory and the router value together.

On vLLM v0.34.0 with ROCm, reloads from the CPU tier and stores to it run at the host-to-device
link rate (about 56 GB/s per GPU), except the first reload after startup (about 2.7 GB/s per GPU).
Through the router, a 63K-token prompt restored from the CPU tier had a time to first token of
0.30 s against 1.4 s to recompute it, the same as a hit in GPU memory; at 232K tokens it was
0.72-0.76 s against 5.1 s.

## Known Limitations

- **vLLM version:** use v0.34.0. Images built from vLLM main after 2026-11-16 fail to load this
  checkpoint until [vllm#68197](https://github.com/vllm-project/vllm/pull/68197) merges.
- **MoRI-IO WRITE mode** (and the sidecar's `--moriio-write-mode` / `--moriio-parallel-dispatch`)
  is not supported for this model; READ mode serializes prefill and decode.
- **Prefill and decode TP must match** (the KDA state is split by TP).
- **Attention data parallelism (DP=8, TP=1)** is not supported yet: with one API server per rank,
  every rank binds the same MoRI-IO handshake port, and the routing sidecar picks the DP rank
  without regard to the prefix cache.
- **Context length:** the default of 262,144 tokens was checked with 232K- and 248K-token prompts
  (correct answers, CPU restore included); AMD's model card documents 64K. Lower `MAX_MODEL_LEN`
  on both roles if your traffic is shorter.
- **MTP speculative decoding** is not enabled; it is untested with MoRI-IO READ mode on this model.
- **HBM size:** 30 GB of KV per GPU is the size validated with MoRI-IO on Pollara; larger values
  have not been validated against the 2 GB registration limit yet.

## Benchmarking

> [!NOTE]
> **TODO:** benchmark profiles and results for this guide are not yet published.

## Cleanup

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:cleanup.modelserver end -->

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
