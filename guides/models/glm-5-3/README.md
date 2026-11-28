# GLM-5.3 Quark MXFP4 on AMD MI355X

## Overview

This guide serves [amd/GLM-5.3-Quark-MXFP4-AttnFP8](https://huggingface.co/amd/GLM-5.3-Quark-MXFP4-AttnFP8/tree/bcd1ec31dc1fd474b9997dd2476c92687790b83d)
on AMD Instinct MI355X with vLLM and the llm-d router, for long-context agentic sessions that keep
hundreds of thousands of tokens in context and add large new turns. The default deployment
disaggregates prefill and decode (P/D): one 8-GPU prefill instance and one 8-GPU decode instance,
with the KV cache moved GPU to GPU by MoRI-IO. Decode then no longer shares engine steps with other
sessions' prefill, which keeps the decode tail low while long prompts are ingested. Colocated
layouts on one node are provided as alternatives for peak throughput and looser tail targets.

It composes [P/D disaggregation](../../pd-disaggregation/README.md) (MoRI-IO READ, prefix-based P/D
decider), [wide expert parallelism](../../wide-ep/README.md) (DP attention with MoRI-EP expert
all-to-all, here inside one node per role) and, for the colocated layouts, the
[optimized baseline](../../optimized-baseline/README.md) routing (prefix-cache affinity and
token-load scoring per DP rank).

The model is AMD's [AMD Quark](https://quark.docs.amd.com/) quantization of
[zai-org/GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) (`GlmMoeDsaForCausalLM`: MLA with
DeepSeek sparse attention, 744B total / 40B active parameters plus one multi-token-prediction (MTP)
layer), served at revision `bcd1ec31dc1fd474b9997dd2476c92687790b83d`. All MLP and expert weights
are MXFP4 and attention, including the sparse indexer, is FP8. At run time the MLA KV cache is BF16
and the indexer key cache FP8: 93,912 bytes per token per rank, including the MTP layer, so a 256k
session needs about 24 GB of KV and a 512k session about 48 GB.

Status: community guide, Tier 2 (Deployable) once the overlays pass the kustomize dry-run CI job.
Gaps to Tier 3: no nightly E2E job, and the engine image is built by the user from the ROCm MAD
recipe (see [Engine image](#engine-image)) rather than published. Benchmark results from the tested
deployment are in [Reference results](#reference-results).

## What You Get

| Parameter | Value |
| --- | --- |
| Model | `amd/GLM-5.3-Quark-MXFP4-AttnFP8`, revision `bcd1ec31` |
| Accelerator | AMD Instinct MI355X (8 GPUs, 288 GB HBM3E each, per node), AMD Pensando RDMA NICs, one rail per GPU |
| Serving topology | P/D, two nodes: one prefill pod and one decode pod, each DP=8 / EP=8 / TP=1 |
| KV transfer | MoRI-IO, decode-initiated RDMA READ, serial dispatch; decode reads only the KV blocks it does not already hold |
| Expert parallel | MoRI-EP inside each role (`mori_high_throughput` on prefill, `mori_low_latency` on decode) |
| Kernels | AITER (MXFP4 MoE, MLA, RMSNorm) |
| Speculative decoding | MTP-3 on both roles |
| KV cache | 80e9 bytes per GPU (851,856 tokens per rank), block size 16, prefix caching on |
| Context | `--max-model-len 560000` |
| Batching | prefill `--max-num-batched-tokens 16384` (eager), decode 256 with CUDA graphs; `--max-num-seqs 64` |
| Routing | prefix-based P/D decider (`nonCachedTokens: 2048`), session-sticky DP rank pinned on both legs |

### Request flow

```text
client ──► Envoy ──ext_proc──► EPP (P/D profiles: picks decode and prefill endpoints)
             │  Lua filter: x-data-parallel-rank = hash(session) mod 8
             ▼
       decode pod :8000  routing sidecar ──1-token prefill leg──► prefill pod :8000 (vLLM, DP8)
             │                         ◄── kv_transfer_params (block IDs) ──┘
             ▼
       decode vLLM :8200 (DP8) ──MoRI-IO RDMA READ of missing blocks──► prefill HBM
             │
             └──► streams tokens back through the sidecar and Envoy
```

1. An Envoy Lua filter, placed before `ext_proc`, sets `x-data-parallel-rank` from a hash of
   `x-session-id` (or `x-correlation-id`) modulo 8, or a random rank for requests without a session
   ID. The header selects the same engine rank on both roles, which keeps each session's retained
   prefix on the rank where its next turn runs. MoRI-IO does not need matching ranks: decode reads
   from the prefill rank named in `kv_transfer_params`.
2. The EPP runs a P/D configuration. `disaggregatedset-rollout-screener` admits only a complete
   prefill and decode revision. `prefix-based-pd-decider` disaggregates when the EPP's estimate
   leaves at least 2,048 input tokens uncached; the estimate comes from the approximate prefix
   index, which matches at most 131,072 tokens. At the context lengths in this guide every
   measured request was disaggregated.
3. `disagg-profile-handler` runs two profiles. Prefill: `prefill-filter` →
   `prefix-cache-affinity-filter` → `token-load-scorer` → `max-score-picker`, fed by
   `approx-prefix-cache-producer` and `inflight-load-producer`. Decode: `decode-filter` →
   `active-request-scorer` → `max-score-picker`.
4. The EPP returns the decode endpoint in `x-gateway-destination-endpoint` and the prefill endpoint
   in `x-llm-d-prefiller-host-port`. Envoy routes to the decode pod's sidecar.
5. The sidecar sends the one-token prefill leg, forwards the returned `kv_transfer_params` to
   decode, and streams the decode response. Decode allocates its blocks, checks its own prefix
   cache, and reads only the missing KV from the prefill rank over RDMA, GPU memory to GPU memory.

The `InferencePool` spans both roles on port 8000: prefill vLLM serves it directly, and on decode
pods it is the sidecar, which forwards to vLLM on 8200.

### Deployments

| `DEPLOYMENT` | Nodes | Layout | KV bytes per GPU | Prefill chunk | Router values |
| --- | ---: | --- | --- | --- | --- |
| `pd-1p1d` (default) | 2 | 1 prefill + 1 decode instance, DP8/EP8 each | 80e9 | prefill 16,384; decode 256 | `glm-5-3.values.yaml` |
| `pd-2p1d` | 3 | 2 independent prefill instances + 1 decode instance, DP8/EP8 each | prefill 80e9, decode 55e9 | prefill 16,384; decode 256 | `glm-5-3.values.yaml` |
| `colocated-ep` | 1 | 2 replicas × 4 GPUs, DP4/EP4, one endpoint per DP rank | 100e9 | 2,048 | `glm-5-3-colocated-ep.values.yaml` |
| `colocated-ep-chunk1024` | 1 | as `colocated-ep` | 100e9 | 1,024 | `glm-5-3-colocated-ep.values.yaml` |
| `colocated-tp` | 1 | 2 replicas × TP4, one endpoint per replica | 149e9 | 2,048 | `glm-5-3-colocated-tp.values.yaml` |

All layouts use MTP-3, block size 16, a 560k-token window, `--max-num-seqs 64` and the same image.
Choose P/D when large new turns arrive against long retained context and an inter-token latency
(ITL) p99 target around 150 ms is the primary objective. Choose a colocated layout when turns add
little new input, when peak tok/s/GPU or an ITL p99 target of 250 ms or looser is the objective
(`colocated-ep-chunk1024` trades prefill speed for a lower decode tail), or when one node is
preferred.

## Supported Accelerators and Model Servers

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | Notes |
| --- | --- | --- | --- | --- |
| AMD MI355X | `amd` | `amd/GLM-5.3-Quark-MXFP4-AttnFP8` | 🟡 community | Default: P/D on 2 nodes, DP8/EP8 prefill + DP8/EP8 decode, MoRI-IO READ (`pd-1p1d`) · `pd-2p1d`: 3 nodes · colocated on 1 node: `colocated-ep`, `colocated-ep-chunk1024`, `colocated-tp` |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

## Prerequisites

- Have the [proper client tools installed on your local system](../../../helpers/client-setup/README.md) to use this guide.

- MI355X nodes (gfx950), 8 GPUs each, with the AMD GPU operator exposing `amd.com/gpu`. P/D needs
  two nodes (three for `pd-2p1d`); the colocated layouts need one. The reference results were
  measured with the amdgpu host driver 6.16.13.

- AMD Pensando RDMA NICs, one rail per GPU, exposed to pods as `amd.com/vnic` through Multus and the
  AMD network operator's CNI. The engines request one rail per GPU. NIC firmware `1.117.5-a-196`
  was used for the reference results; the image's AINIC userspace (`AINIC_VERSION` in the
  Dockerfile) must match your firmware.

- Netdev MTU 9000 on every rail (RDMA path MTU 4096; `ibv_devinfo` reports `active_mtu: 4096`), and
  RoCE QoS configured end to end. Set your fabric's `MORI_RDMA_SL` and `MORI_RDMA_TC` in
  [`components/fabric`](./modelserver/amd/vllm/components/fabric/kustomization.yaml).

- An RDMA NetworkAttachmentDefinition named `glm-5-3-rdma` in the namespace. Edit
  [`network/rdma-nad.yaml`](./modelserver/amd/vllm/network/rdma-nad.yaml) for your CNI before the
  network step below.

- The engine image, built from the ROCm MAD Dockerfile and pushed to a registry your nodes can pull
  from; set it in [`components/image`](./modelserver/amd/vllm/components/image/kustomization.yaml)
  (see [Engine image](#engine-image)).

- A Gateway API Inference Extension bundle that supports multiple `targetPorts` (`v1.5.0-rc.2` or
  newer; measured with `v1.6.2`).

- For the P/D deployments: the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/)
  `v0.11.1` or newer with `--set enableDisaggregatedSet=true` (measured with LWS `1.0.0`).

- A default StorageClass with `ReadWriteMany` for the 1 TiB model cache (`model-pvc`).

- `python3` on the machine that runs `helm` (the router install uses a post-renderer).

- Create a [HuggingFace token](../../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

### Get the guide

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

**Set the guide-specific environment variables** (`DEPLOYMENT` picks the layout):

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=glm-5-3
export NAMESPACE=llm-d-glm-5-3
export ACCELERATOR_TYPE=amd # options: amd
export MODEL_SERVER=vllm # options: vllm
export DEPLOYMENT=pd-1p1d # options: pd-1p1d, pd-2p1d, colocated-ep, colocated-ep-chunk1024, colocated-tp
export MODEL=amd/GLM-5.3-Quark-MXFP4-AttnFP8
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

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../../helpers/hf-token.md):

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

**Create the RDMA NetworkAttachmentDefinition** (after editing it for your CNI):

<!-- guide:prerequisites.network start -->
```bash
# RDMA rail attachment the engines reference as glm-5-3-rdma; edit spec.config for your CNI first
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/amd/vllm/network/rdma-nad.yaml
```
<!-- guide:prerequisites.network end -->

**Download the model** into `model-pvc`. The engines and the benchmark Job read it offline at the
pinned revision (141 safetensors shards):

<!-- guide:prerequisites.model start -->
```bash
# model-pvc (RWX, 1 TiB) and a one-time download of the pinned checkpoint (410 GB)
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/amd/vllm/model-storage
kubectl wait -n ${NAMESPACE} --for=condition=complete --timeout=6h job/download-glm-5-3
```
<!-- guide:prerequisites.model end -->

## Installation Instructions

### 1. Deploy the llm-d Router

<!-- guide:deploy.router_values start -->
```bash
# P/D: prefix-based P/D decider, prefill and decode profiles, and the Envoy Lua filter
# that pins both legs of a request to the same DP rank
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
export ROUTER_OVERRIDES_VALUES="${ROUTER_VALUES}"
export ROUTER_POST_RENDERER="--post-renderer ${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/session-sticky/post-render.py"

# only when DEPLOYMENT=colocated-ep or colocated-ep-chunk1024:
# One endpoint per DP rank, prefix-cache affinity and token-load scoring, no rank pin
export ROUTER_OVERRIDES_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}-colocated-ep.values.yaml"
export ROUTER_POST_RENDERER="${ROUTER_POST_RENDERER} --post-renderer-args=--dp-ranks=0"

# only when DEPLOYMENT=colocated-tp:
# One endpoint per TP4 replica, prefix-cache affinity and token-load scoring, no rank pin
export ROUTER_OVERRIDES_VALUES="${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}-colocated-tp.values.yaml"
export ROUTER_POST_RENDERER="${ROUTER_POST_RENDERER} --post-renderer-args=--dp-ranks=0"
```
<!-- guide:deploy.router_values end -->

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  -f ${ROUTER_VALUES} \
  -f ${ROUTER_OVERRIDES_VALUES} \
  ${ROUTER_POST_RENDERER} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

The router values pin the EPP and Envoy images the reference results were measured with (see
[Images](#images)), set `--allow-experimental-plugins` (the DisaggregatedSet rollout screener is
Alpha), and set `router.proxy.failOpen: false`.

**Fail closed.** With fail-open, Envoy forwards requests around the EPP whenever its `ext_proc`
cluster is unhealthy: responses stay correct, but scheduling and disaggregation stop without an
error. This guide fails closed, so requests are refused until the EPP is healthy.
The trade-off is that an EPP outage shows up as client errors instead of as silently
non-disaggregated serving. The reference results were measured fail-open behind the same health
check; set `router.proxy.failOpen: true` if you prefer availability over strict routing.
The [verification](#verification) step waits for Envoy's `ext_proc` membership to report healthy;
run it again after every router restart or upgrade, before sending load.

**Post-renderer.** [`router/session-sticky/post-render.py`](./router/session-sticky/post-render.py)
edits the chart's Envoy configuration, which is a single templated string that values cannot
extend. For P/D it inserts the Lua rank filter ahead of `ext_proc` (`--dp-ranks`, default 8); for
the colocated layouts it runs with `--dp-ranks=0`, which skips the filter. For every layout it sets
the `ext_proc` health check's `no_traffic_interval` and `unhealthy_interval` to 5 s, as in the
reference runs, so the router recovers within seconds of an EPP restart. It exits with an error if
it cannot find its anchors exactly once.

**Prefix index sizing.** `approx-prefix-cache-producer` hashes prompts in 64-token router blocks
(`blockSizeTokens: 64`) without tokenizing; this is independent of the engine's 16-token KV blocks.
`lruCapacityPerServer` therefore counts 64-token router blocks per endpoint, not engine blocks or
tokens: engine KV tokens per endpoint / 64. A P/D endpoint is a whole DP8 pod (851,856 tokens per
rank × 8 / 64 ≈ 106,484); a colocated expert-parallel endpoint is one DP rank (1,064,816 / 64 =
16,637); a colocated tensor-parallel endpoint is one TP4 replica (24,786). If you change
`KV_CACHE_MEMORY_BYTES`, recompute it from the KV tokens the engine logs at start-up.

### 2. Deploy the Model Server

Set your image in [`components/image`](./modelserver/amd/vllm/components/image/kustomization.yaml)
first; the checked-in name is a placeholder.

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}

# First start JIT-compiles the AITER, Triton and MoRI kernels (~30 min); the startup probe allows 2 h
kubectl wait -n ${NAMESPACE} --for=condition=Ready pod -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/inference-serving=true --timeout=2h
```
<!-- guide:deploy.modelserver end -->

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Check the Router and Send a Request

The first check waits until Envoy treats the EPP as healthy. The second asks for an answer whose
content or reasoning must contain `391`; a broken decode can still return HTTP 200, so the check
reads the content.

<!-- guide:verify.tests start -->
```bash
# Envoy must see the EPP (ext_proc) as healthy; until then the router refuses requests
EPP_IP=$(kubectl -n ${NAMESPACE} get endpointslices \
  -l kubernetes.io/service-name=${GUIDE_NAME}-epp \
  -o jsonpath='{.items[0].endpoints[0].addresses[0]}')
kubectl run extproc-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="EPP_IP=${EPP_IP}" \
  -- /bin/sh -c 'for i in $(seq 1 30); do
      curl -s "http://${EPP_IP}:19001/stats/prometheus" \
        | grep -q "^envoy_cluster_membership_healthy{envoy_cluster_name=\"ext_proc\"} 1" \
        && { echo "OK: ext_proc healthy"; exit 0; }
      sleep 5
    done; echo "FAIL: ext_proc not healthy"; exit 1'

# A real answer through the router: max_tokens=1 is an error under the glm45 reasoning parser
kubectl run serve-check --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'Q="{\"role\": \"user\", \"content\": \"What is 17 times 23?\"}"
    B="{\"model\": \"${MODEL}\", \"max_tokens\": 1024, \"temperature\": 0, \"messages\": [${Q}]}"
    R=$(curl -sS -m 600 -X POST "http://${IP}/v1/chat/completions" -H "Content-Type: application/json" -d "${B}")
    echo "${R}" | grep -q "391" \
      && echo "OK: ${MODEL} answered through the router" \
      || { echo "FAIL: ${R}"; exit 1; }'
```
<!-- guide:verify.tests end -->

In the engine logs, `Using AITER_MXFP4_MXFP4 backend for w_mxfp4_a_mxfp4` confirms the native MXFP4
expert kernels; `EMULATION` there means they were not selected.

### 3. Accuracy and KV-Transfer Gates (P/D)

Run these once per P/D deployment, before benchmarking. Every request must exercise the KV
transfer, so switch the router to the always-disaggregate decider first, and switch back afterwards:

```bash
helm upgrade ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} -f ${ROUTER_VALUES} \
  -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/router/${GUIDE_NAME}-pd-accuracy.values.yaml \
  ${ROUTER_POST_RENDERER} -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Re-run the `ext_proc` check above, then run the [Benchmarking](#benchmarking) setup and execute
steps with `BENCH_RUN=(--from-literal=MODE=gates)` in place of the workload definition. The Job runs
the ROCm MAD `benchmark_gates.sh` gates in order:

| Gate | What it does | Pass (reference) |
| --- | --- | --- |
| KV-transfer probe on every rank | 2 rounds × 16 session IDs balanced over the 8 ranks; `/metrics` deltas on every engine | every request 200; prefill generates 1 token per request; decode computes 1 prompt token per request locally and receives the rest by KV transfer; every decode rank served |
| GSM8K | lm-eval 0.4.13 `local-completions`, 5-shot, all 1,319, strict match | 91.28% |
| Needle in a haystack | 64k / 256k / 512k × depth 0 / 0.5 / 1 × 2 needles | 18/18 |
| Multi-turn needle | sessions at ~246k and ~490k, 3 turns of +10k tokens | 7/8 and 8/8 |

Also check, outside the probe, that every rank keeps at least 20 GiB of free GPU memory after the
first transfer (`amd-smi` on the node), and that decode's MTP acceptance rate is 0.45-0.60 with an
accepted length of 2.35-2.75. Needle results varied between deployments of the same software (18/18
on the reported deployment, 51/54 across three); that variation is under evaluation.

## Benchmarking

The benchmark Job runs the [ROCm MAD](https://github.com/ROCm/MAD/pull/304) long-context scripts
(`scripts/vllm_dissag` at a pinned commit) through the llm-d router Service, from a CPU-only pod
that uses the engine image as the client. It writes `/results/<run id>/` and `/results/perf.csv` on
the `glm-5-3-bench` PVC.

- **Warm multi-turn sessions** (`MODE=multiturn`, `benchmark_multiturn_retained.sh`): each session
  holds `RETAINED_TOKENS` of cached context (the conversation so far, including earlier replies) and
  each turn adds `NEW_TOKENS` of new input. Every session runs one warm-up turn and then two measured
  turns per round, for six rounds; every reply is exactly 1,000 tokens. Session IDs are sent as
  `X-Session-ID` and balanced over the eight ranks, so the router keeps each session on one rank.
  The text is Project Gutenberg #1184, fetched at run time and SHA256-checked.
- **Cold prompts** (`MODE=cold`, the cell of `benchmark_long_context.sh`): exact-length random
  prompts of `PROMPT_TOKENS` with nothing cached (prefix caches reset first), 1,000 output tokens.
- **Gates** (`MODE=gates`): see [Accuracy and KV-transfer gates](#3-accuracy-and-kv-transfer-gates-pd).

`multiturn_metrics.py` reduces each run:

- **tok/s/GPU**: output tokens per second while all sessions are active, divided by every GPU of the
  deployment (`GPUS`: 16 for `pd-1p1d`, 24 for `pd-2p1d`, 8 for a colocated node).
- **Engine ITL**: decode-step gaps from the `vllm:inter_token_latency_seconds` histogram of the
  decode role (or the colocated engines), over rounds 2-6. One sample per decode step; with MTP-3 a
  step delivers about 2.6 tokens. Percentiles are interpolated inside the histogram buckets (edges
  at 50, 75, 100, 150, 200, 300 and 400 ms in this range); 150 ms is a bucket edge, so a 150 ms
  verdict does not depend on interpolation.
- **Client ITL**: exact gaps between streamed chunks as the client sees them, plus TTFT.
- **Goodput** at an ITL target of 150, 250 or 500 ms: tok/s/GPU when engine ITL p99 is at most the
  target **and** client TTFT p90 meets the TTFT target for the retained context (12.8 s at 128k,
  25.6 s at 256k, 51.2 s at 512k; cold prompts use the prompt length); otherwise 0.

The setup step defines one run in the `glm-5-3-bench-run` ConfigMap: by default the 256k context,
+30k-per-turn workload (16 sessions on `pd-1p1d`, 8 per colocated node) or, on `pd-2p1d`, cold 256k
prompts. To run another point, edit the `--from-literal` values; the measured points were:

| Workload | `RETAINED_TOKENS` / `NEW_TOKENS` or `PROMPT_TOKENS` | P/D sessions (`CONCURRENCY`) | Colocated sessions per node |
| --- | --- | ---: | ---: |
| 128k context, +10k per turn | 128000 / 10000 | 32 | 16 |
| 128k context, +30k per turn | 128000 / 30000 | 32 | 16 |
| 128k context, +70k per turn | 128000 / 70000 | 16 | 8 |
| 256k context, +30k per turn | 256000 / 30000 | 16 | 8 |
| 256k context, +70k per turn | 256000 / 70000 | 16 | 8 |
| 512k context, +30k per turn | 512000 / 30000 | 8 (also 4 and 2) | 4 (also 2 and 1) |
| Cold 256k prompt | 256000, `NUM_PROMPTS` = 3 × concurrency | 12 on `pd-2p1d` | 4 |
| Cold 512k prompt | 512000, `NUM_PROMPTS` = 3 × concurrency | 4 on `pd-1p1d`, 6 on `pd-2p1d` | 2 |

<!-- guide:benchmark.setup start -->
```bash
# only when DEPLOYMENT=pd-1p1d or pd-2p1d:
# Engine /metrics endpoints: prefill vLLM on 8000, decode vLLM on 8200 (behind the sidecar)
sel="llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role"
METRICS_ENDPOINTS="$(kubectl get pods -n ${NAMESPACE} -l ${sel}=prefill \
  -o jsonpath='{range .items[*]}prefill={.status.podIP}:8000 {end}')$(kubectl get pods -n ${NAMESPACE} -l ${sel}=decode \
  -o jsonpath='{range .items[*]}decode={.status.podIP}:8200 {end}')"

# only when DEPLOYMENT=colocated-ep or colocated-ep-chunk1024:
# One /metrics endpoint per DP rank
METRICS_ENDPOINTS=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/inference-serving=true \
  -o jsonpath='{range .items[*]}coloc={.status.podIP}:8000 coloc={.status.podIP}:8001 coloc={.status.podIP}:8002 coloc={.status.podIP}:8003 {end}')

# only when DEPLOYMENT=colocated-tp:
METRICS_ENDPOINTS=$(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/inference-serving=true \
  -o jsonpath='{range .items[*]}coloc={.status.podIP}:8000 {end}')

# only when DEPLOYMENT=pd-1p1d:
# 256k retained context, +30k new tokens per turn, 16 sessions on 16 GPUs
BENCH_RUN=(--from-literal=MODE=multiturn --from-literal=RETAINED_TOKENS=256000 --from-literal=NEW_TOKENS=30000
           --from-literal=CONCURRENCY=16 --from-literal=GPUS=16 --from-literal=LAYOUT=pd)

# only when DEPLOYMENT=pd-2p1d:
# Cold 256k-token prompts, 12 in flight, 36 measured, on 24 GPUs
BENCH_RUN=(--from-literal=MODE=cold --from-literal=PROMPT_TOKENS=256000 --from-literal=NUM_PROMPTS=36
           --from-literal=CONCURRENCY=12 --from-literal=GPUS=24 --from-literal=LAYOUT=pd)

# only when DEPLOYMENT=colocated-ep or colocated-ep-chunk1024 or colocated-tp:
# 256k retained context, +30k new tokens per turn, 8 sessions on the node's 8 GPUs
BENCH_RUN=(--from-literal=MODE=multiturn --from-literal=RETAINED_TOKENS=256000 --from-literal=NEW_TOKENS=30000
           --from-literal=CONCURRENCY=8 --from-literal=GPUS=8 --from-literal=LAYOUT=coloc)

# The run definition, then the Job, its script and the results PVC
kubectl create configmap glm-5-3-bench-run -n ${NAMESPACE} "${BENCH_RUN[@]}" \
  --from-literal=METRICS_ENDPOINTS="${METRICS_ENDPOINTS}" --dry-run=client -o yaml | kubectl apply -f -
kubectl delete job glm-5-3-bench -n ${NAMESPACE} --ignore-not-found=true
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/benchmark
```
<!-- guide:benchmark.setup end -->

<!-- guide:benchmark.execute start -->
```bash
kubectl wait -n ${NAMESPACE} --for=condition=complete --timeout=8h job/glm-5-3-bench
kubectl logs -n ${NAMESPACE} job/glm-5-3-bench | grep -E 'MT_RESULT|goodput|\[STALL\]' | tail -n 20
```
<!-- guide:benchmark.execute end -->

## Reference Results

> [!NOTE]
> Reference results from the tested deployment (November 5, 2026), not a guarantee. They are
> functional-enablement results: kernel, batching and parallelism tuning is ongoing, and results on
> your hardware, software and workload may vary. They were measured on an engine image built from
> the same vLLM commit and patches as the ROCm MAD Dockerfile, using vLLM's own ROCm build recipe;
> an image built from the MAD Dockerfile has not been measured yet.

P/D (`pd-1p1d`, 16 GPUs; `pd-2p1d`, 24 GPUs) is compared with independently measured 8-GPU
colocated nodes at equal GPU count. The colocated control for each workload is the
higher-throughput of `colocated-ep` and `colocated-tp`, both with 2,048-token prefill chunks.
Colocated runs route to per-rank endpoints with prefix-affinity and load scoring rather than a
session pin. ITL figures are engine ITL unless stated.

| Workload | P/D tok/s/GPU | Colocated control tok/s/GPU | Ratio | P/D TTFT p90 | P/D ITL p99 / p99.9 | Outcome (150 ms ITL and TTFT targets) |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 128k context, +10k per turn | 70.4 | 92.5 (tensor parallel) | 0.76× | 8.9 s | 311 / 599 ms | Colocated ahead |
| 128k context, +30k per turn | 55.0 | 60.7 (expert parallel, one node) | 0.91× | 19.9 s | 129 / 180 ms | Colocated ahead; P/D TTFT above 12.8 s |
| 128k context, +70k per turn | 23.9 | 29.6 (expert parallel) | 0.81× | 24.0 s | 50 / 287 ms | Colocated ahead; P/D TTFT above 12.8 s |
| 256k context, +30k per turn | 31.8 | 37.6 (expert parallel) | 0.85× | 14.0–14.1 s | 71–73 / 145–149 ms | **Only P/D meets 150 ms** |
| 256k context, +70k per turn | 21.1 | 29.0 (expert parallel) | 0.73× | 27.6 s | 65 / 287 ms | Colocated ahead; P/D TTFT above 25.6 s |
| 512k context, +30k per turn | 16.6 | 18.1 (expert parallel) | 0.92× | 12.3 s | 50 / 185 ms | Both meet every target; colocated ahead |
| Cold 256k prompt, 2P1D | 5.5 | 6.7 (tensor parallel, one node) | 0.82× | 91.8 s | 50 / 1,516 ms | Neither meets 25.6 s TTFT |
| Cold 512k prompt, 1P1D | 1.61 | 2.43 (expert parallel) | 0.66× | 172.4 s | 50 / 50 ms | Neither meets 51.2 s TTFT |
| Cold 512k prompt, 2P1D | 1.65 | 2.44 (expert parallel, three nodes) | 0.68× | 146.7 s | 50 / 1,666 ms | Neither meets 51.2 s TTFT |

On the 256k context, +30k per turn workload:

- Engine ITL p99 is 71–73 ms with P/D versus 316–322 ms for `colocated-ep`, and p99.9 is
  145–149 ms versus 392 ms. Decode steps slower than 150 ms are 0.01–0.08% of steps with P/D versus
  1.3–1.4% colocated. At the client, p99 is about 91 ms versus 332–335 ms and p99.9 144–150 ms
  versus 369–370 ms.
- At a 150 ms target P/D delivers 31.8 tok/s/GPU and colocated serving 0. At 500 ms every
  expert-parallel layout qualifies: P/D 31.8, colocated 37.6–37.7 tok/s/GPU. P/D TTFT p90 is
  14.0 s versus 6.2 s colocated, inside the 25.6 s target.
- `colocated-ep-chunk1024` (one node) measured ITL p99 190 ms, TTFT p90 7.2 s and 37.7 tok/s/GPU
  (on 256k context, +70k per turn: 213 ms, 16.0 s and 28.2 tok/s/GPU). It meets a 250 ms target
  with higher throughput than P/D, but not 150 ms.
- Colocated ITL p99 on this workload is sensitive to how often decode overlaps another session's
  prefill: nearly all colocated gaps are either short decode steps or 300–400 ms prefill stalls,
  so p99 reads about 50–90 ms or 310–360 ms depending on whether stalls are under or over 1% of
  gaps. The stall share and p99.9 are the steadier comparison.

512k context, +30k per turn, by concurrent sessions (P/D / colocated tok/s/GPU): 2 sessions
4.54 / 5.11, 4 sessions 8.07 / 10.10, 8 sessions 16.6 / 18.1. At 16 sessions each P/D GPU would
hold two 512k sessions, about 1.117M tokens, above its 851,856-token KV budget.

With MoRI-IO READ, the bytes received on the decode NICs matched new tokens × 93,912 on the warm
workloads that add 30k–70k tokens per turn and on the cold 256k prompt: only new KV moves.
Accuracy on the reported deployment: GSM8K 5-shot 91.28%, needle in a haystack 18/18 (51/54 across
three deployments), multi-turn needle 7/8 at ~246k and 8/8 at ~490k.

### Images

| Component | Image |
| --- | --- |
| Engine (tested) | vLLM 0.32 development build at `8673602b` (public `cd1effa2e7` plus the MAD recipe's patches), ROCm 10.0, PyTorch `2.13.0+rocm10.0.0`, AITER `v0.1.27`, MoRI `a5ac2bc9`, AINIC userspace `1.117.5-a-196`, gfx950. Build it from the [ROCm MAD Dockerfile](#engine-image) |
| EPP | `ghcr.io/llm-d/llm-d-router-endpoint-picker@sha256:fadd5d9ea21a6c9d0a1515b93f0f442a8a7d73f02f394e6bc0c5bf9accc54b9c` (llm-d main branch) |
| Routing sidecar | `ghcr.io/llm-d/llm-d-router-disagg-sidecar@sha256:ed2f80ed776720fa70331105cf67be32e01344d241f329ba9d83096a1dab49d5` (llm-d main branch), `--kv-connector=nixlv2` |
| Envoy | `docker.io/envoyproxy/envoy:distroless-v1.40.2@sha256:09ca6f2140a61ad8df11779a4a30d52b48433d444466328494f9188c9c35eded` |
| Router chart | `llm-d-router-standalone` `v0` |

## Cleanup

<!-- guide:cleanup start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/deployments/${DEPLOYMENT}

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/models/${GUIDE_NAME}/benchmark --ignore-not-found=true
kubectl delete configmap glm-5-3-bench-run -n ${NAMESPACE} --ignore-not-found=true
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/models/${GUIDE_NAME}/modelserver/amd/vllm/network/rdma-nad.yaml --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->

The downloaded model stays on `model-pvc` until the namespace is deleted.

## Engine Image

The engine is vLLM on ROCm with the patch set in the ROCm MAD recipe, proposed in
[ROCm/MAD#304](https://github.com/ROCm/MAD/pull/304):

- Dockerfile: [`docker/vllm_disagg_inference.glmv5.3_quark_mxfp4_attnfp8.ubuntu.amd.Dockerfile`](https://github.com/shikamd123/MAD/blob/glm53-mxfp4-llmd-vllm-dockerfile/docker/vllm_disagg_inference.glmv5.3_quark_mxfp4_attnfp8.ubuntu.amd.Dockerfile)
- Recipe, with every layout's `vllm serve` flags, environment, router configuration, workloads and
  gates: [`scripts/vllm_dissag/glm-5.3-quark-mxfp4-attnfp8.md`](https://github.com/shikamd123/MAD/blob/glm53-mxfp4-llmd-vllm-dockerfile/scripts/vllm_dissag/glm-5.3-quark-mxfp4-attnfp8.md)

```bash
git clone https://github.com/ROCm/MAD.git && cd MAD
git fetch origin pull/304/head:glm53 && git checkout glm53
docker build -f docker/vllm_disagg_inference.glmv5.3_quark_mxfp4_attnfp8.ubuntu.amd.Dockerfile \
  --build-arg AINIC_VERSION=<your NIC firmware version> \
  -t <your-registry>/vllm-disagg:glmv5.3_quark_mxfp4_attnfp8 .
docker push <your-registry>/vllm-disagg:glmv5.3_quark_mxfp4_attnfp8
```

The Dockerfile starts from a public vLLM ROCm base image, builds MoRI at `a5ac2bc9` and vLLM at
`cd1effa2e7` for gfx950, and applies each patch in
`docker/patches/vllm_disagg_inference.glmv5.3_quark_mxfp4_attnfp8/` (one file per change, with its
upstream pull request and removal condition). The patches cover MoRI-IO with block-split MLA and
indexer KV views, ROCm `fused_add_rms_norm` for remote-prefilled sparse-attention decode under CUDA
graphs, the MoRI-EP V1 IntraNode receive-row bound with its `VLLM_MORI_RECV_ROW_ALIGN` setting, and
MoRI-IO READ of only the KV blocks decode does not already hold. The image carries no serving
settings; everything is passed by these manifests. Once the patches are in a vLLM release built
with a MoRI that contains ROCm/mori#736, the image can be replaced with a published vLLM ROCm image.

Then set `newName` and `newTag` (or `digest`) in
[`components/image`](./modelserver/amd/vllm/components/image/kustomization.yaml). If your registry
needs credentials, create a pull secret and add it to the `glm-5-3` ServiceAccount after deploying.

## Troubleshooting

- **Requests fail with 5xx right after a router install or restart.** The router fails closed while
  Envoy's `ext_proc` cluster is unhealthy. Re-run the `ext_proc` check; it becomes healthy within
  seconds of the EPP becoming ready.
- **One router release per namespace.** The chart names its Envoy ConfigMap `envoy` regardless of
  the release name, so a second release in the same namespace overwrites the first one's Envoy
  configuration.
- **KV budget on P/D.** Registering the KV cache for RDMA lowered the free GPU memory reported by the
  driver by about the KV size on the tested driver (amdgpu 6.16.13), without a matching allocation.
  Keep explicit KV bytes (`KV_CACHE_MEMORY_BYTES`) and at least 20 GiB free per rank after the first
  transfer; that is why P/D uses 80e9 bytes per GPU and `pd-2p1d` decode, which registers once per
  prefill instance, 55e9.
- **`VLLM_MORI_RECV_ROW_ALIGN=4096` is required** with the image's MoRI-EP receive-row bound; do not
  remove it from the expert-parallel layouts.
- **`--all2all-backend mori` selects MoRI-EP V2**, which does not accept this checkpoint's per-tensor
  activation quantization at engine init. The manifests set `VLLM_MORI_EP_V2=0`.
- **Session rank pin.** The Lua filter keeps a session on one DP rank on both roles for
  prefix-cache locality; MoRI-IO itself reads from whichever prefill rank computed the KV. Leave
  `--moriio-dp-size` off the sidecar: it replaces the prefill rank with a per-request hash, which
  breaks per-session prefix locality on prefill. READ with parallel dispatch
  (`--moriio-parallel-dispatch`) is not supported.
- **Persistent JIT caches.** The engines compile into an `emptyDir` (~30 min on first start). If you
  move `/var/cache/vllm` to a persistent volume, a pod killed during an AITER JIT build leaves lock
  files (`aiter_jit/build/lock_*`, `lock`, `.ninja_lock`) that the next pod waits on; delete them
  before the engine starts.
- **Engine logs on P/D.** LWS recreates the whole pod when the engine exits, so
  `kubectl logs --previous` is empty after a crash; send the engine output to a volume if you need it.

## Tuning

- **KV budget.** `KV_CACHE_MEMORY_BYTES` sets the per-GPU KV cache; keep the router's
  `lruCapacityPerServer` equal to the engine's KV tokens per endpoint / 64 (see
  [Prefix index sizing](#1-deploy-the-llm-d-router)).
- **Router calibration.** `peakPrefillThroughput` is a per-endpoint estimate. Re-measure it with
  [`guides/recipes/router/calibration`](../../recipes/router/calibration/) for your layout.
- **MTP capture sizes.** With MTP-k, keep every `CUDAGRAPH_CAPTURE_SIZES` entry a multiple of k+1 and
  the largest at least `max-num-seqs × (k+1)`; larger steps run without CUDA graphs.
- **Another fabric.** Set `MORI_RDMA_SL` / `MORI_RDMA_TC` in `components/fabric` to your fabric's
  QoS, and drop `MORI_ENABLE_RAIL_ONLY` / `MORI_ENABLE_HOST_PROXY` where your fabric is fully
  connected and the NIC supports GPU-initiated RDMA. The
  [wide-ep AMD base](../../wide-ep/modelserver/amd/vllm/base/disaggregatedset.yaml) documents each
  MoRI knob.
- **Other KV transfer modes.** MoRI-IO WRITE (`--moriio-write-mode` on the sidecar) and WRITE with
  parallel dispatch are available but were not measured here.
