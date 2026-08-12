# P2P KV Cache Sharing

Well-lit path for peer-to-peer KV cache sharing: any vLLM instance pulls
cached prefix KV blocks directly from a peer's CPU offload tier instead of
recomputing them.

## Overview

This guide deploys `openai/gpt-oss-120b` with peer-to-peer KV cache
sharing. The transfer is CPU-to-CPU over NIXL (UCX/RDMA when available).
The source pod's GPU is never touched, so serving a pull costs the source
no prefill capacity.

The deployment composes three llm-d capabilities:

* the vLLM `OffloadingConnector` with a P2P secondary tier (each pod is
  both a puller and a source),
* the llm-d Router's precise (KV-event-fed) prefix index, which the
  source decision consumes, and
* the `p2p-source-producer`, which stamps each request with the peer that
  holds the most cached prefix; the routing sidecar injects
  `kv_transfer_params.remote_kv_source` and the engine pulls instead of
  recomputing.

The example deploys 16 TP=1 replicas on 16 GPUs (aggregated). A P/D
variant - pull on the prefill leg - is described at the end and reuses
the [P/D disaggregation guide](../pd-disaggregation/README.md)'s
topology.

### When to use this path

P2P sharing pays wherever routing cannot, or should not, send every
request to the pod that already caches its prefix:

* **Load must spread.** A hot shared prefix saturates its cache owner
  under affinity routing. Load-aware routing plus the pull spreads the
  work while preserving cache reuse.
* **The working set exceeds any single pod's cache.** With N pods each
  caching 1/N of the prefix pool, cross-pod requests either recompute or
  pull.
* **Many concurrent sessions pinned to owner pods.** Sessions queue
  behind a busy owner or spill to a colder pod that recomputes, even
  when aggregate GPU capacity has room. The guide's document Q&A
  headline is this case.
* **Long prefixes.** The pull is a near-constant-time copy; recompute
  grows with length. Measure the crossover for your model (the benchmark
  below does) and route pulls only above it.
* **Multi-turn sessions on P/D disaggregation.** Decode generates the
  session history, so on every turn the prefill worker faces KV it never
  computed and no routing decision can make local. The pull lets prefill
  fetch decode's generated KV directly: **6.3x median TTFT and +50%
  throughput** on a 2P+4D Qwen3-30B rig
  ([report](benchmark-results/qwen3-30b-h200-pd-agentic.md)). This is
  the largest measured effect in the guide, growing with history length
  and turn count. Requires `offload_prompt_only: false` on decode and a
  chat template that re-renders generated answers verbatim (see Best
  Practices).

What the pull is worth depends on the placement in front of it:

* **Affinity + P2P** (the shipped default) sends each request to the pod
  that already holds its prefix, so the pull rarely fires; it acts as a
  fallback for the requests placement displaces. Its measured throughput
  delta over affinity alone is within run-to-run spread, so do not
  choose this arm expecting the pull to add throughput. It also does not
  recover a restarted router: both prefix indexes lose the pre-restart
  cache map, and measured restart-recovery runs produced zero pulls.
* **Load-aware + P2P** deliberately scatters requests, and the pull is
  what makes scattering affordable. The pull's own margin is the matched
  `load` versus `load + P2P` pair: +143% sustained rate on the uniform
  pool, +224% with zero client timeouts on the hot set. Comparing
  against affinity changes placement too, so it measures the deployment,
  not the pull alone: on the document Q&A headline (128 concurrent
  multi-turn sessions, each pinned to an owner pod) load-aware + P2P
  beats affinity warm by +35% throughput and 1.5x better p99 TTFT, and
  on a cold fleet finishes with zero client timeouts against affinity's
  47-48. On the uniform shared-prefix pool the ordering flips: affinity
  stays ahead (p50 0.48 s vs 0.73 s at 30 req/s) because nothing
  contends and a local hit is free.
* **P/D + P2P** addresses KV no placement decision could have made
  local (see the multi-turn bullet above).

The guide ships affinity + P2P as the general-purpose default. Reach
for load-aware + P2P when your workload
looks like many concurrent sessions pinned to owner pods, and re-measure
both arms on your own workload before assuming either generalizes.
Measured tables:
[benchmark-results/gpt-oss-120b-h200.md](benchmark-results/gpt-oss-120b-h200.md).

## Configuration

### Router scheduling configurations

Four EPP scheduling configurations ship with the guide (under
[benchmarking/](benchmarking/)). The recommended deployment is
`epp-affinity-p2p.yaml`; the others are the comparison arms the guide's
measurements use:

| Config | Placement | Pull |
|---|---|---|
| [`epp-affinity-p2p.yaml`](benchmarking/epp-affinity-p2p.yaml) | precise prefix-cache affinity | `p2p-source-producer`, `minCachedTokenDelta: 2048` (recommended - see the placement rule above) |
| [`epp-load-p2p.yaml`](benchmarking/epp-load-p2p.yaml) | load-balanced | `p2p-source-producer` (for high-concurrency, session-ownership-bound workloads) |
| [`epp-affinity.yaml`](benchmarking/epp-affinity.yaml) | precise prefix-cache affinity | none (baseline) |
| [`epp-load.yaml`](benchmarking/epp-load.yaml) | load-balanced | none (recompute control) |

`minCachedTokenDelta` is the minimum lead, in cached prefix tokens, a
peer must hold over the scheduled pod before a pull is requested. Set it
from the measured pull-versus-recompute crossover: 2,048 on this guide's
testbed (gpt-oss-120b and Llama-8B both cross near or below 2K), 12,288
on the wide-EP GLM-5.2 testbed. The crossover is model-, hardware- and
transport-specific, so re-measure it when any of those change, on a
warmed pod pair (the first pull between two peers pays a one-time
session-establishment cost). The measurement is automated:
[guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh](../recipes/router/calibration/calibrate-min-cached-token-delta.sh)
runs it against two live pods and prints the recommended value.

### Supported Hardware Backends

* NVIDIA GPU / vLLM. Measured on H200; any CUDA GPU with enough HBM for
  the model works.

Every benchmark in this guide was measured with `rdma/ib` exposed to the
model-server containers, and that is the recommended configuration. RDMA
is not required: NIXL/UCX falls back to TCP and the pull still works.
But the transport sets the pull-versus-recompute crossover, so it
changes `minCachedTokenDelta`. On TCP the pull leg inflates while
recompute is unchanged, moving the crossover from below 2K tokens to
roughly 29K. Measured single-request prefill-latency delta on
gpt-oss-120b (negative means the pull wins):

| prefix tokens | with `rdma/ib` (canonical) | without |
|---:|---:|---:|
| 2,048 | -55.8% | +26.7% |
| 8,192 | -77.4% | +20.2% |
| 16,384 | -83.2% | +10.9% |
| 32,768 | -85.9% | -4.9% |
| 49,152 | -88.2% | -15.3% |

With RDMA the pull wins at every measured length, so
`minCachedTokenDelta: 2048` follows. Without it, the same deployment
needs a value an order of magnitude larger and only benefits workloads
whose reused prefixes are that long. Check whether `rdma/ib` is present
on your pods before reading the ladder across, and derive the value from
a crossover measured on your own transport.

## Best Practices

* Keep the offloading block size identical across all P2P peers. This
  guide omits `kv_connector_extra_config.block_size`, so it defaults to
  the engine's `--block-size`; therefore `--block-size` must be
  identical across all model-server pods in this deployment. If an
  explicit offloading `block_size` is configured instead, use the same
  value on every peer and ensure that it is a multiple of each peer's
  engine block size. The router's `tokenProcessorConfig.blockSize` is
  an independent indexing granularity and may differ.
* `PYTHONHASHSEED` pinned to the same value fleet-wide. vLLM seeds block
  hashes per process; unpinned seeds mean no block hash ever matches
  across pods and every lookup misses.
* `--kv-events-config` on every serving pod, topic
  `kv@<POD_IP>:<PORT>@<model>`. No events, no precise index, no source
  selection. `<PORT>` must be the port the router identifies the
  endpoint by: the routing sidecar's port (`8000` in this guide), not
  the engine port (`8200`). The EPP matches the topic against the
  InferencePool endpoint; a mismatched port leaves the index empty, so
  no pull ever fires. This bites when adapting the manifest to a
  different port layout, not the shipped one.
* Matched TP between peers that serve each other. The peer session
  fingerprint embeds the parallel layout, so a TP-mismatched pair
  rejects the session and requests silently recompute. Hetero-TP works
  only for non-hybrid-attention models on the V1 model runner
  (`VLLM_USE_V2_MODEL_RUNNER=0` where V2 is the default); in-review
  upstream work stores offloaded KV in a parallelism-free layout
  ([vllm#48414](https://github.com/vllm-project/vllm/pull/48414)),
  removing the coupling.
* **Multi-pod data-parallel groups (LWS wide-EP) must compensate the
  socket base ports per pod.** vLLM binds the P2P and KV-events
  listeners at `configured base + global data_parallel_index`; the
  router addresses `pod IP + pod-local rank`. Those agree when each pod
  is its own DP group (every topology in this guide). They disagree for
  worker pods of a multi-pod group, and a mis-addressed pull does not
  fall back to recompute - it stalls the request until the client times
  out. Each pod subtracts its global start rank from both bases:

  ```bash
  START_RANK=$(( ${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))
  P2P_BASE=$((7777 - START_RANK))        # P2P secondary tier port
  KV_EVENTS_BASE=$((5557 - START_RANK))  # KV-events publisher endpoint
  ```

  The router must also attribute KV events to the publishing rank's
  endpoint
  ([llm-d-router#2233](https://github.com/llm-d/llm-d-router/pull/2233))
  and the sidecar must compare full endpoints in its self-pull guard
  ([llm-d-router#2234](https://github.com/llm-d/llm-d-router/pull/2234));
  run a router build that carries both before enabling the pull on such
  a topology. The GLM results in this guide did.
* `offload_prompt_only` set to match what peers can use. Prefix pulls
  work under either setting; `false` additionally offloads *generated*
  KV so a conversation's full history is pullable. Pair `false` with the
  precise index and a chat template that re-renders answers verbatim.
  This guide's deployment runs `false`; the wide-EP testbed runs `true`
  because its model drops reasoning on re-render, so generated KV is
  unreusable regardless.
* CPU tier (`cpu_bytes_to_use`) larger than the per-pod GPU KV cache -
  2x as the working default. The tier's value is the KV that GPU evicts
  and CPU *retains* (the
  [tiered path's](../../docs/well-lit-paths/foundations/tiered-prefix-cache.md)
  receptive field): a smaller tier mostly duplicates blocks that are
  still GPU-resident, and the router's view of who holds a prefix
  outruns what sources can actually serve.
  * Compute the ratio from measured KV capacity, not per-GPU intuition.
    Weights are paid once per pod while KV memory scales with TP, so
    per-pod KV capacity grows superlinearly with the TP degree.
    gpt-oss-120b on H200 at `--gpu-memory-utilization=0.85`: TP=1
    leaves ~55 GB of KV (~1.4M tokens); TP=4 leaves ~414 GB (~10M
    tokens), so a 128 GiB tier is 2.3x the GPU cache at TP=1 and 0.33x
    at TP=4. Read the KV capacity from the engine startup log and size
    the tier from it, per role.
  * Size `/dev/shm` above `cpu_bytes_to_use` (the tier is an shm mmap)
    and the pod memory limit above both - the memory-backed emptyDir
    counts against the pod's limit.
  * With data parallelism (`--data-parallel-size` N > 1), each DP
    replica gets its own tier region and P2P port: `/dev/shm` must
    exceed N x `cpu_bytes_to_use`, and rank `r` listens on the
    configured port + `r`. Requires vLLM with per-DP-rank P2P ports and
    per-replica offload regions
    ([vllm#47636](https://github.com/vllm-project/vllm/pull/47636),
    [vllm#47987](https://github.com/vllm-project/vllm/pull/47987)).
* The render Service (`render/`) fronts the model servers themselves:
  vLLM serves `/v1/*/render` natively, so render capacity scales with
  the serving fleet
  ([llm-d#2188](https://github.com/llm-d/llm-d/pull/2188)). The Service
  targets the vLLM port directly; the pods' port 8000 belongs to the
  routing-proxy sidecar, which does not serve `/render`. When serving
  CPU is contended, apply `render/standalone/` instead - a dedicated
  GPU-less pool under the same Service name - and size it to the
  request rate: one replica saturates near 10 req/s at ~50K-token
  prompts, and past saturation every request stalls for the
  token-producer `vllm.timeout` (default 5 s), then routes without
  token IDs - prefix scoring silently disabled while engines sit idle.
  Alert on flat TTFT plateaus at the timeout value.
* Set an explicit client timeout in benchmark workloads
  (`load.request_timeout`); compare stage wall-clock to send-window +
  drain, not to the offered duration.

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
export GUIDE_NAME="p2p-kv-cache-sharing"
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

Additional requirements specific to this path:

* A vLLM image with the `OffloadingConnector` P2P secondary tier (see
  the [release floor](#engine-image-vllm-release-floor)).
* An llm-d routing sidecar that injects
  `kv_transfer_params.remote_kv_source`. A sidecar emitting the older
  `p2p`/`prefill`/`decode` keys is silently inert against current
  engines - see Troubleshooting.

## Installation Instructions

### 1. Prepare HF Token

Create the `llm-d-hf-token` secret in the namespace. The router reads
`HF_TOKEN` to reach gated tokenizers; `openai/gpt-oss-120b` is public,
but the secret makes swapping in a gated model a no-op. See
[helpers/hf-token.md](../../helpers/hf-token.md).

```bash
export HF_TOKEN=<your HuggingFace token>
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 2. Deploy the llm-d Router

Install the router with this guide's values, which deploy the EPP with
`epp-affinity-p2p.yaml` as the default. To run a comparison arm instead,
swap the `pluginsCustomConfig` in the values for another config from
[benchmarking/](benchmarking/).

```bash
helm upgrade -i ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

#### Deploy the Render (Tokenizer) Service

The EPP `token-producer` tokenizes prompts by calling vLLM's
`/v1/completions/render` endpoint through the render Service:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render
```

The default overlay fronts the model servers themselves; when serving
CPU is contended, apply `render/standalone/` instead and size it per
the [Best Practices](#best-practices) render bullet.

### 3. Deploy the Model Server

Apply the Kustomize overlay for your transport:

```bash
export ACCELERATOR_TYPE=gpu   # options: gpu
export MODEL_SERVER=vllm      # options: vllm
export TRANSPORT=rdma         # options: rdma (recommended), base
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TRANSPORT}/
```

16 replicas, TP=1, `--block-size=64`, KV events on, the offloading
connector with a P2P tier on port 7777.

- **`rdma`** adds an `rdma/ib` device and `IPC_LOCK` to every model
  server. Every benchmark in this guide was measured on it, and it is
  the recommended overlay.
- **`base`** is the same deployment without the IB device. NIXL/UCX
  falls back to TCP; the pull still works, but the crossover moves to
  ~29K tokens and `minCachedTokenDelta` has to move with it. See
  [Supported Hardware Backends](#supported-hardware-backends).

The `rdma/ib` resource name is what the measured clusters expose; yours
may differ (`rdma/hca`, `nvidia.com/rdma`, ...). Check before applying,
and edit `modelserver/gpu/vllm/rdma/patch-rdma.yaml` to match:

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | tr ',' '\n' | grep -i rdma
```

Confirm the device actually reached the container. A pod that schedules
without it serves normally and just pulls slowly, so this failure looks
like a performance result rather than a misconfiguration:

```bash
kubectl exec -n ${NAMESPACE} deploy/p2p-kv-cache-sharing-decode -c modelserver -- ls /dev/infiniband
```

#### Engine image: vLLM release floor

The `OffloadingConnector` P2P secondary tier and its robustness fixes
([vllm#48021](https://github.com/vllm-project/vllm/pull/48021),
[vllm#49671](https://github.com/vllm-project/vllm/pull/49671),
[vllm#49823](https://github.com/vllm-project/vllm/pull/49823),
[vllm#49877](https://github.com/vllm-project/vllm/pull/49877)) and the
block-table width alignment fix that wide-EP `GLM-5.2` deployments (the
[GLM results](./benchmark-results/glm-5.2-h200.md) testbed) need
([vllm#50302](https://github.com/vllm-project/vllm/pull/50302)) are all
contained in vLLM `v0.27.0`, the first tagged release with the full
set; no source overlay is required. The kustomization pins `v0.27.1`.

### 4. Calibrate `minCachedTokenDelta` for your model and transport

The shipped EPP configs set `minCachedTokenDelta: 2048`, the crossover
measured for this guide's reference setup (gpt-oss-120b on H200 with
`rdma/ib`). On any other combination, measure your own against the pods
you just deployed:

```bash
NAMESPACE=${NAMESPACE} \
POD_SELECTOR=llm-d.ai/guide=p2p-kv-cache-sharing \
MODEL_NAME=openai/gpt-oss-120b \
${REPO_ROOT}/guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh
```

The recipe prints the recommended value; set it on the
`p2p-source-producer` in the router values, re-apply, and restart the
EPP. See
[Calibrating `minCachedTokenDelta`](../recipes/router/calibration/README.md#calibrating-mincachedtokendelta)
for what it measures and its prerequisites.

### 5. (Optional) Enable Monitoring

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).
- To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#2-deploy-the-llm-d-router).
- Deploy the monitoring resources for model servers:

  ```bash
  kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
  ```

## Verification

### 1. Get the IP of the Proxy

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send Test Requests

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    -- curl -X POST http://${IP}:8081/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{"model": "openai/gpt-oss-120b", "prompt": "How are you today?"}'
```

### 3. Mechanism-engaged gates

An inert misconfiguration looks identical to "no effect": requests serve
fine, nothing pulls. Run every gate before trusting any measurement:

1. **Render live**: the Service must return token IDs through the same DNS
   name used by the EPP. This fails on an empty selector, a wrong
   `targetPort`, or an unavailable render API:

   ```bash
   kubectl run render-check --rm -i --restart=Never \
     --image=python:3.12-alpine --namespace="$NAMESPACE" -- \
     python -c '
   import json, urllib.request
   data = json.dumps({"model": "openai/gpt-oss-120b", "prompt": "render check", "max_tokens": 1}).encode()
   request = urllib.request.Request("http://p2p-kv-cache-sharing-render:8000/v1/completions/render", data=data, headers={"Content-Type": "application/json"})
   with urllib.request.urlopen(request, timeout=10) as response:
       body = json.load(response)
   assert isinstance(body, list) and body and body[0].get("token_ids"), body
   print(body[0]["token_ids"])
   '
   ```

2. **Index populated**: the EPP logs show KV-event subscriptions for
   every pod; a scheduling decision logs non-zero prefix scores.
3. **Header firing**: the routing sidecar logs
   `running P2P source protocol` with a `source_host` on requests whose
   prefix a peer holds.
4. **Pulls landing**: `vllm:external_prefix_cache_hits_total` rises on
   pulling pods; at `VLLM_LOGGING_LEVEL=DEBUG` the source logs each
   served fetch. See
   [Measuring pull activity](benchmarking/README.md#measuring-pull-activity).
5. **Hash agreement**: seed one pod with a prefix, request it on another
   with the header; a hit of ~the full prefix length proves block hashes
   match (if zero, check `PYTHONHASHSEED` and `--block-size`).

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) - the supported standard CLI for llm-d performance benchmarking.

### 1. Install the `llmdbenchmark` CLI

The guide's workload profile ships via
[llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656),
which is still open, so until it merges the profile comes from the PR
fork at a pinned commit:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
cd llm-d-benchmark
git fetch https://github.com/nilig/llm-d-benchmark.git \
    960f55a910fc4c049428b820b54462227dfda510
git checkout 960f55a910fc4c049428b820b54462227dfda510
source .venv/bin/activate
llmdbenchmark --version
```

### 2. Resolve the endpoint of the stack you just deployed

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}'):8081"
export GATEWAY_CLASS=epponly # standalone mode
```

### 3. Run the benchmark profile for P2P KV Cache Sharing

`guide_p2p-kv-cache-sharing_1.yaml` is a document-Q&A workload profile
for this guide. Run it once per routing arm, switching only the EPP
configuration between runs:

```bash
llmdbenchmark \
    --spec           guides/p2p-kv-cache-sharing \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_p2p-kv-cache-sharing_1.yaml \
    --analyze
```

The full scenario matrix (crossover micro-benchmark, shared-prefix
pools, hot set, document Q&A) with its measured tables and the A/B
protocol lives in [benchmarking/README.md](benchmarking/README.md).

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TRANSPORT}/
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render
```

## How It Works

1. **Model server pods publish KV-cache events** and run the
   `OffloadingConnector` with a CPU tier plus a P2P secondary tier:
   every pod both offloads its computed KV to CPU and serves it to
   peers.
2. **The router builds its prefix index** - here the precise one from
   the KV events - so it knows which pods hold which prefix blocks.
3. **The `p2p-source-producer` compares** the best-cached peer against
   the pod scheduling picked; when the peer leads by at least
   `minCachedTokenDelta` tokens it sets the KV cache source header.
4. **The routing sidecar injects `kv_transfer_params.remote_kv_source`**
   from the header and the engine pulls the prefix blocks from the
   peer's CPU tier over NIXL. Hits load as normal cache hits; ordinary
   misses recompute, so a request whose peer does not have the blocks
   degrades to baseline behavior rather than failing.

   > [!WARNING]
   > That fallback covers ordinary misses, not a write that never
   > lands. On the pinned engine a block left in `HIT_PENDING` has no
   > deadline, so a request waiting on it can stay deferred until the
   > client times out. Treat a stalled `HIT_PENDING` as a known
   > limitation on current engines.

## P/D variant: P2P over NIXL disaggregation

Measured on this topology: **6.3x median TTFT and +50% throughput**
against plain NIXL P/D on a multi-turn agentic workload -
[full report](benchmark-results/qwen3-30b-h200-pd-agentic.md).

Under P/D disaggregation the pull applies to the prefill leg only: the
prefill worker computes the prompt KV and streams it to the decoder, so
that is the leg where recomputing a cached prefix is wasted work. The
decode leg already receives the full KV over NIXL and has nothing to
pull.

Start from the [P/D disaggregation guide](../pd-disaggregation/README.md)
topology and change three things:

1. **Engines run `MultiConnector`** - NIXL carries the P/D transfer, the
   OffloadingConnector provides the CPU tier and the P2P listener. Same
   config on both legs (a pod serves pulls regardless of role):

   ```json
   {"kv_connector":"MultiConnector","kv_role":"kv_both",
    "kv_connector_extra_config":{"connectors":[
      {"kv_connector":"NixlConnector","kv_role":"kv_both"},
      {"kv_connector":"OffloadingConnector","kv_role":"kv_both",
       "kv_connector_extra_config":{"spec_name":"TieringOffloadingSpec",
        "cpu_bytes_to_use":94489280512,"offload_prompt_only":false,
        "secondary_tiers":[{"type":"p2p","host":"$(POD_IP)","port":7777}]}}]}}
   ```

   Both side channels must bind the pod IP via the downward API:
   `VLLM_NIXL_SIDE_CHANNEL_HOST` and `VLLM_P2P_SIDE_CHANNEL_HOST`. All
   prerequisites from [Best Practices](#best-practices) apply
   unchanged. Size `cpu_bytes_to_use` **per role**: decode legs
   typically run higher TP, so their per-pod GPU KV (and the tier that
   must exceed it) is several times a prefill pod's; the value above is
   a prefill-leg (TP=1) size.

2. **The routing sidecar declares the tier** with
   `--kv-connector=nixlv2 --enable-p2p-pull` (plus
   `--p2p-connector-port=7777` if not the default). `--enable-p2p-pull`
   is accepted only with `--kv-connector=nixlv2`; with
   `--kv-connector=offloading` the tier is native and the flag is
   unnecessary.

3. **The EPP scheduling config targets the prefill profile**: set the
   `p2p-source-producer`'s `prefillProfileName` to the disaggregation
   prefill profile name (default `prefill`), so the source comparison
   runs against the pod that will actually compute the prefix.

Size the decode pool for its NIXL intake - each request ships its full
KV from prefill to decode, and that intake, not prefill placement, is
typically the topology's ceiling.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No pulls, everything serves; EPP logs `bestCachedTokens:0` for every request | index empty: block-size mismatch, missing kv-events, kv-events topic port not matching the router's endpoint port, or hash disagreement (`PYTHONHASHSEED`) | verification gates 1 and 4 |
| `rejecting peer connect: block_len mismatch` | `--block-size` differs between pods | align it everywhere |
| No pulls from a TP-mismatched source, index and hashes fine | peer session fingerprint is TP-locked | matched TP; hetero-TP only for non-hybrid models on the V1 runner (Best Practices) |
| Pulls fire but hit rate ~0 | CPU tier too small vs GPU cache; prefixes evicted before peers ask | grow `cpu_bytes_to_use` (and `/dev/shm`) |
| Sidecar exits with `unknown flag: --enable-p2p-pull` | sidecar image predates the NIXL PD pull path | use a sidecar build that includes it |
| Zero pulls, gates 1-2 pass | sidecar emits the old sub-dict keys (`p2p`/`prefill`/`decode`); the engine ignores them | use a sidecar built with the renamed keys (`remote_kv_source`/`remote_prefiller`/`remote_decoder`) |
| TTFT pins flat at ~the token-producer timeout (default 5 s) at every rate above some cliff; engines report near-zero queue/prefill time; both arms identical | render capacity saturated; every EPP render call times out and requests proceed late without token IDs | apply `render/standalone/` and scale it per Best Practices; verify with a direct load test against `/v1/completions/render` |

## Benchmarking Reports

Benchmark reports comparing the routing arms under identical hardware:

- **[openai/gpt-oss-120b on vLLM (H200, aggregated)](./benchmark-results/gpt-oss-120b-h200.md)**:
  pull-versus-recompute crossover, shared-prefix pools, and the document
  Q&A headline.
- **[Qwen/Qwen3-30B-A3B-Thinking on vLLM (H200, P/D agentic)](./benchmark-results/qwen3-30b-h200-pd-agentic.md)**:
  prefill pulling decode's generated session history - 6.3x median TTFT
  and +50% throughput against plain NIXL P/D.
- **[zai-org/GLM-5.2-FP8 on vLLM (H200, wide-EP P/D)](./benchmark-results/glm-5.2-h200.md)**:
  the mechanism at 753B - the load-spill payoff (-67% mean TTFT, 2.7x
  throughput for a load-first policy with the pull versus without),
  crossover sweep, and the index-sizing failure-mode record.
