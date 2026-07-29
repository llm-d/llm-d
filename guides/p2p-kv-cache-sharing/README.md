# P2P KV Cache Sharing

Well-lit path for peer-to-peer KV cache sharing: any vLLM instance pulls
cached prefix KV blocks directly from a peer's CPU offload tier instead of
recomputing them.

## Overview

This guide deploys `openai/gpt-oss-120b` with peer-to-peer KV cache sharing.
The transfer is CPU-to-CPU over NIXL (UCX/RDMA when available) - the source
pod's GPU is never touched, so serving a pull costs the source no prefill
capacity.

The deployment composes three llm-d capabilities:

* the vLLM `OffloadingConnector` with a P2P secondary tier (each pod is both
  a puller and a source),
* the llm-d Router's prefix index - this guide deploys the precise
  (KV-event-fed) one; the approximate (prompt-hash) index drives the source
  decision equally well (measured on the wide-EP testbed), and
* the `p2p-source-producer`, which stamps each request with the peer that
  holds the most cached prefix so the routing sidecar can inject
  `kv_transfer_params.remote_kv_source` and the engine pulls instead of
  recomputing.

In this example we deploy 16 TP=1 replicas on 16 GPUs (aggregated). A P/D
variant - pull on the prefill leg - is described at the end and reuses the
[P/D disaggregation guide](../pd-disaggregation/README.md)'s topology.

### When to use this path

P2P sharing pays wherever routing cannot (or should not) send every request
to the pod that already caches its prefix:

* **Load must spread.** A hot shared prefix saturates its cache owner under
  affinity routing; load-aware routing plus a pull spreads the work while
  preserving cache reuse.
* **The working set exceeds any single pod's cache.** With N pods each
  caching 1/N of the prefix pool, cross-pod requests either recompute or
  pull.
* **High session concurrency under a fixed ownership rule.** Many
  concurrent multi-turn sessions, each pinned by affinity to the pod that
  computed its prefix, can still queue behind a busy owner or spill to a
  colder pod that recomputes - independent of whether aggregate GPU
  capacity has room to spare. This guide's own document-Q&A headline is
  this case.
* **Long prefixes.** The pull is a near-constant-time CPU-to-CPU copy while
  recompute grows with length. Measure the crossover for your model (the
  benchmark below does); route pulls only above it.
* **Multi-turn sessions on P/D disaggregation.** Decode generates the
  session history, so on every subsequent turn the prefill worker faces KV
  it never computed and no routing decision can make local - without a pull
  it re-prefills the whole accumulated history each turn. The pull lets the
  prefill leg fetch decode's generated KV directly (see the
  [P/D variant](#pd-variant-p2p-over-nixl-disaggregation)). This is the
  regime with the largest measured gains, growing with history length and
  turn count - agentic sessions with 10K-100K-token contexts most of all:
  **6.3x median TTFT and +50% throughput** on a 2P+4D Qwen3-30B rig
  ([report](benchmark-results/qwen3-30b-h200-pd-agentic.md); the gains are
  median and throughput, not the extreme tail).
  Requires `offload_prompt_only: false` on decode and a chat template that
  re-renders generated answers verbatim (see Best Practices); models that
  drop reasoning content on re-render cannot reuse generated KV regardless
  of the serving stack.

The placement rule this guide's measurements support is more textured than
a single trigger, and the two scenarios where this guide measured
affinity + P2P against load-aware + P2P point opposite ways. On the
uniform shared-prefix pool (Scenario A: a working set spread wider than
any one pod's cache) affinity + P2P tracks offered rate to saturation and
matches affinity's near-ideal latency (p50 0.48 s at 30 req/s), while
load-aware + P2P runs a constant factor behind (p50 0.73 s, ~2% lower
achieved) - precise placement concentrates each prefix's traffic tightly
enough that requests hit locally, while scattering pays a pull on every
displaced request; the pull keeps that price sub-second (against a 63 s p50
recompute floor without it), but affinity never pays it at all. On the document-Q&A
headline (128 concurrent multi-turn sessions, each pinned by affinity to
the pod that computed its prefix) the result flips: load-aware + P2P
avoids the owner-pod queueing that affinity + P2P still pays, and its
p95/p99 tail beats affinity + P2P by 2x or more in both measured runs (see
[benchmark-results/gpt-oss-120b-h200.md](benchmark-results/gpt-oss-120b-h200.md)
for both). Affinity + P2P is the safer general-purpose default - close to
affinity's ceiling in the capacity-driven regime and still a clear
improvement over affinity alone in the concurrency-driven one - which is
why the guide ships it. Reach for load-aware + P2P specifically when your
workload looks like many concurrent, multi-turn sessions each pinned to an
owner pod; re-measure both arms against your own workload shape before
assuming either generalizes.

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

`minCachedTokenDelta` is set from the measured pull-versus-recompute
crossover (see [Benchmarking](#benchmarking)): a pull is requested only when
a peer holds at least that many more cached prefix tokens than the scheduled
pod. The crossover is per model - 2,048 on this testbed (gpt-oss-120b and
Llama-8B both cross near or below 2K), 16,384 on the wide-EP GLM-5.2
testbed (753B; tie measured at 13.6K tokens) - so re-measure it when
changing models, on a warmed pod pair (the first pull between two peers
pays a one-time session-establishment transient). The measurement is
automated as a calibration recipe:
[guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh](../recipes/router/calibration/calibrate-min-cached-token-delta.sh)
runs it against two live pods and prints the recommended value.

### Supported Hardware Backends

* NVIDIA GPU / vLLM (measured on H200; any CUDA GPU with enough HBM for the
  model works). **Every benchmark in this guide was measured with `rdma/ib`
  exposed to the model-server containers**, and that is the recommended
  configuration. RDMA is not required for the feature to work - NIXL/UCX
  falls back to TCP - but the transport sets the pull-versus-recompute
  crossover, so it changes `minCachedTokenDelta` rather than whether the
  pull functions. On TCP the pull leg inflates while recompute is unchanged,
  moving the crossover from below 2K out to **between 16K and 32K tokens**
  (~29K on a finer sweep). Measured on
  gpt-oss-120b, same image and configuration, `rdma/ib` the only difference -
  TTFT delta, negative means the pull wins:

  | prefix tokens | with `rdma/ib` (this guide) | without |
  |---:|---:|---:|
  | 2,048 | -49% | +26.7% |
  | 8,192 | -76% | +20.2% |
  | 16,384 | -83% | +10.9% |
  | 32,768 | -86% | -4.9% |
  | 49,152 | -88% | -15.3% |

  With RDMA the pull wins at every length measured, so `minCachedTokenDelta:
  2048` follows. Without it the pull loses below that crossover and wins
  above it, so the
  same deployment needs a `minCachedTokenDelta` an order of magnitude larger
  and only benefits workloads whose reused prefixes are that long. Check
  whether `rdma/ib` is present on your pods before reading the Step 0 ladder
  across, and derive the value from a crossover measured on your own
  transport.

## Best Practices

* `--block-size` identical on every pod AND in the router's
  `precise-prefix-cache-producer` (`tokenProcessorConfig.blockSize`). A
  mismatch leaves the prefix index empty and the whole path silently inert -
  requests still serve, nothing pulls.
* `--kv-events-config` on every serving pod, topic
  `kv@<POD_IP>:<PORT>@<model>`. No events, no precise index, no source
  selection from it. (Deployments on the approximate index skip this
  prerequisite - the source decision then runs on prompt-hash estimates.)
  `<PORT>` must be the port the router identifies the endpoint by - the
  routing sidecar's port (`8000` in this guide), not the engine port
  (`8200`). The EPP attributes each event's cached blocks to an endpoint
  by matching the topic's `<POD_IP>:<PORT>` against the InferencePool
  endpoint; a mismatch (e.g. tagging the engine port) leaves the index
  empty for every real endpoint, so `bestCachedTokens` is always 0 and no
  pull ever fires - silently, exactly like "no effect". This bites when
  adapting the manifest to a different port layout, not the shipped one.
* `PYTHONHASHSEED` pinned to the same value fleet-wide. vLLM seeds block
  hashes per process; unpinned seeds mean no block hash ever matches across
  pods and every lookup misses.
* Matched TP between peers that serve each other. The peer session
  fingerprint embeds the parallel layout, so a TP-mismatched pair rejects
  the session and requests silently recompute. Hetero-TP is supported only
  for non-hybrid-attention models on the V1 model runner
  (`VLLM_USE_V2_MODEL_RUNNER=0` where V2 is the default); in-review
  upstream work stores offloaded KV in a canonical parallelism-free layout
  ([vllm#48414](https://github.com/vllm-project/vllm/pull/48414)),
  removing the TP coupling.
* `offload_prompt_only` set to match what peers can use. Prompt-side
  prefix pulls work under either setting; `false` additionally offloads
  *generated* KV (session answers) so a conversation's full history is
  pullable - pair it with an index that covers decode blocks (the precise
  one) and a chat template that re-renders answers verbatim. This guide's
  deployment runs `false`; the wide-EP testbed runs `true` because its
  model drops reasoning on re-render, so generated KV is unreachable for
  reuse regardless.
* CPU tier (`cpu_bytes_to_use`) considerably larger than the per-pod GPU
  KV cache - 2x as the working default. The tier's value is the KV that
  GPU evicts and CPU *retains* (the
  [tiered path's](../../docs/well-lit-paths/foundations/tiered-prefix-cache.md)
  receptive field): a tier smaller than the GPU cache mostly duplicates
  blocks that are still GPU-resident, and the router's view of "who has
  this prefix" outruns what sources can actually serve.
  * **Compute the ratio from measured KV capacity, not per-GPU
    intuition - TP changes it drastically.** Model weights are paid once
    per pod while KV memory scales with the TP degree, so per-pod KV
    capacity grows superlinearly with TP. gpt-oss-120b on H200 at
    `--gpu-memory-utilization=0.85`: TP=1 leaves ~55 GB of KV (~1.4M
    tokens), but TP=4 leaves ~414 GB (~10M tokens). A 128 GiB tier is
    2.3x the GPU cache at TP=1 and 0.33x at TP=4 - large-looking, yet
    unable to hold even the GPU's own evictions. Read the KV capacity
    from the engine startup log and size the tier from it, per role.
  * Size `/dev/shm` above `cpu_bytes_to_use` (the tier is an shm mmap)
    and the pod memory limit above both - the memory-backed emptyDir
    counts against the pod's limit.
  * With data parallelism (`--data-parallel-size` N > 1), each DP
    replica gets its own tier region and P2P port: `/dev/shm` must
    exceed N x `cpu_bytes_to_use`, and rank `r`'s P2P tier listens on
    the configured port + `r`. Requires vLLM with per-DP-rank P2P
    ports and per-replica offload regions
    ([vllm#47636](https://github.com/vllm-project/vllm/pull/47636),
    [vllm#47987](https://github.com/vllm-project/vllm/pull/47987)).
* Size the render service for the request rate. The router's
  `token-producer` calls the render endpoint
  (`/v1/completions/render`) once per request to tokenize the full
  prompt; at ~50K-token prompts one render replica is effectively
  single-core-bound and saturates near 10 req/s. Past saturation every
  request stalls for exactly the token-producer `vllm.timeout` (default
  5s) before routing proceeds without token IDs - prefix scoring is
  silently disabled while engines sit idle. Provision roughly
  `peak_req_per_s x per-request tokenize seconds` in replicas (a 50K
  random-text prompt costs ~0.1s) and alert on flat TTFT plateaus at
  the timeout value.
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

* A vLLM image with the `OffloadingConnector` P2P secondary tier.
* llm-d routing sidecar with `kv_transfer_params.remote_kv_source` injection
  (the branch renamed the sub-dict keys on 2026-07-20; a sidecar emitting the
  old `p2p`/`prefill`/`decode` keys against the current branch is silently
  inert - see Troubleshooting).

## Installation Instructions

### 1. Prepare HF Token

Create the `llm-d-hf-token` secret in the namespace. The router reads
`HF_TOKEN` to reach gated tokenizers - `openai/gpt-oss-120b` is public but
the secret makes swapping in a gated model a no-op. See
[helpers/hf-token.md](../../helpers/hf-token.md) for the full helper.

```bash
export HF_TOKEN=<your HuggingFace token>
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 2. Deploy the llm-d Router

Install the router with this guide's values, which deploy the EPP with the
affinity + P2P scheduling configuration (`epp-affinity-p2p.yaml`) as the
default. To run a comparison arm instead, swap the `pluginsCustomConfig` in
the values for `epp-affinity.yaml`, `epp-load.yaml`, or `epp-load-p2p.yaml`
from [benchmarking/](benchmarking/).

```bash
helm upgrade -i ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

#### Deploy the Render (Tokenizer) Service

The EPP `token-producer` tokenizes prompts by calling vLLM's
`/v1/completions/render` endpoint, served from a dedicated horizontally
scalable Service:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render
```

Size it per the [Best Practices](#best-practices) render bullet - long-prompt
workloads need more replicas than the default.

### 3. Deploy the Model Server

Apply the Kustomize overlay for your transport:

```bash
export ACCELERATOR_TYPE=gpu   # options: gpu
export MODEL_SERVER=vllm      # options: vllm
export TRANSPORT=rdma         # options: rdma (recommended), base
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TRANSPORT}/
```

16 replicas, TP=1, `--block-size=64`, KV events on, the offloading connector
with a P2P tier on port 7777.

- **`rdma`** adds an `rdma/ib` device and `IPC_LOCK` to every model server.
  This is what every benchmark in this guide was measured on, and it is the
  recommended overlay.
- **`base`** is the same deployment without the IB device. NIXL/UCX falls
  back to TCP, which does not stop the pull working - it moves the
  pull-versus-recompute crossover from below 2K tokens out to ~29K, so
  `minCachedTokenDelta` has to move with it. See
  [Supported Hardware Backends](#supported-hardware-backends).

The `rdma/ib` resource name is what the clusters this was measured on expose;
yours may differ (`rdma/hca`, `nvidia.com/rdma`, ...). Check before applying,
and edit `modelserver/gpu/vllm/rdma/patch-rdma.yaml` to match:

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | tr ',' '\n' | grep -i rdma
```

Confirm the device actually reached the container - a pod that schedules
without it serves requests normally and just pulls slowly, so this failure
looks like a performance result rather than a misconfiguration:

```bash
kubectl exec -n ${NAMESPACE} deploy/p2p-kv-cache-sharing-decode -c modelserver -- ls /dev/infiniband
```

#### Engine image: upstream nightly pins

The `OffloadingConnector` P2P secondary tier is upstream in vLLM
([vllm#48021](https://github.com/vllm-project/vllm/pull/48021)); no source
overlay is required. The kustomization pins
`nightly-6f91edf96d3f3272945809c04702380053bff4de`, the first nightly
containing the tier together with all three robustness fixes: the
finalization and reconnect crash fixes
([vllm#49671](https://github.com/vllm-project/vllm/pull/49671),
[vllm#49823](https://github.com/vllm-project/vllm/pull/49823)) and the
symmetric-fetch stall fix under sustained many-to-many pull load
([vllm#49877](https://github.com/vllm-project/vllm/pull/49877)). Prefer a
tagged vLLM release over any nightly once the tier ships in one.

### 4. Calibrate `minCachedTokenDelta` for your model and transport

The shipped EPP configs set `minCachedTokenDelta: 2048`, the crossover
measured for this guide's reference setup (gpt-oss-120b on H200 with
`rdma/ib`). The crossover is model-, hardware- and transport-specific, so on
any other combination measure your own against the pods you just deployed
and set it on the `p2p-source-producer` in the router values:

```bash
NAMESPACE=${NAMESPACE} \
POD_SELECTOR=llm-d.ai/guide=p2p-kv-cache-sharing \
MODEL_NAME=openai/gpt-oss-120b \
${REPO_ROOT}/guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh
```

The recipe prints the recommended value; re-apply the router release with it
and restart the EPP. See
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

An inert misconfiguration looks identical to "no effect" - requests serve
fine, nothing pulls. Run every gate before trusting any measurement:

1. **Index populated**: the EPP logs show KV-event subscriptions for every
   pod; a scheduling decision logs non-zero prefix scores.
2. **Header firing**: the routing sidecar logs
   `running P2P source protocol` with a `source_host` on requests whose
   prefix a peer holds.
3. **Pulls landing**: `vllm:external_prefix_cache_hits_total` rises on
   pulling pods; the source logs the served fetch.
4. **Hash agreement**: seed one pod with a prefix, request it on another
   with the header; a hit of ~the full prefix length proves block hashes
   match (if this is zero, check `PYTHONHASHSEED` and `--block-size`).

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) - the supported standard CLI for llm-d performance benchmarking.

### 1. Install the `llmdbenchmark` CLI

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```

### 2. Resolve the endpoint of the stack you just deployed

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}'):8081"
export GATEWAY_CLASS=epponly # standalone mode
```

### 3. Run the benchmark profile for P2P KV Cache Sharing

`guide_p2p-kv-cache-sharing_1.yaml` is the dedicated workload profile
shipped with `llm-d-benchmark` for this guide - the document Q&A scenario
behind the headline numbers in the
[benchmarking report](#benchmarking-reports). Run it once per routing arm,
switching only the EPP configuration between runs:

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

The full scenario matrix (crossover micro-benchmark, shared-prefix pools,
hot set, document Q&A) with its measured tables and the A/B protocol lives
in [benchmarking/README.md](benchmarking/README.md).

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${TRANSPORT}/
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render
```

## How It Works

1. **Model server pods publish KV-cache events** and run the
   `OffloadingConnector` with a CPU tier plus a P2P secondary tier: every
   pod both offloads its computed KV to CPU and serves it to peers on the
   P2P port.
2. **The router builds its prefix index** - in this deployment the precise
   one from the KV events - so it knows per request which pods hold which
   prefix blocks.
3. **The `p2p-source-producer` compares** the best-cached peer against the
   pod scheduling actually picked; when the peer leads by at least
   `minCachedTokenDelta` tokens it sets the KV cache source header.
4. **The routing sidecar injects `kv_transfer_params.remote_kv_source`** from the header
   and the engine pulls the prefix blocks from the peer's CPU tier over
   NIXL - hits load as normal cache hits, misses recompute, so a failed
   transfer degrades to baseline behavior instead of failing the request.

## P/D variant: P2P over NIXL disaggregation

Measured on this topology: **6.3x median TTFT and +50% throughput** against
plain NIXL P/D on a multi-turn agentic workload -
[full report](benchmark-results/qwen3-30b-h200-pd-agentic.md).

Under P/D disaggregation the pull applies to the **prefill leg only**: the
prefill worker computes the prompt KV and streams it to the decoder, so
that is the leg where recomputing a cached prefix is wasted work. The EPP
evaluates the source header against the prefill profile's target, and the
sidecar injects `kv_transfer_params.remote_kv_source` onto the prefill leg; the decode
leg already receives the full KV over NIXL and has nothing to pull.

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
   other prerequisites from [Best Practices](#best-practices) (block size,
   `PYTHONHASHSEED`, `offload_prompt_only: false`, CPU-tier sizing) apply
   unchanged - and size `cpu_bytes_to_use` **per role**: decode legs
   typically run higher TP, so their per-pod GPU KV (and therefore the
   tier that must exceed it) is several times a prefill pod's. The value
   in the example above is a prefill-leg (TP=1) size; see the CPU-tier
   bullet in Best Practices for the TP arithmetic.

2. **The routing sidecar declares the tier** with
   `--kv-connector=nixlv2 --enable-p2p-pull` (plus
   `--p2p-connector-port=7777` if not the default). `--enable-p2p-pull` is
   accepted only with `--kv-connector=nixlv2`; the sidecar rejects it with
   any other connector at startup (with `--kv-connector=offloading` the
   tier is native and the flag is unnecessary).

3. **The EPP scheduling config targets the prefill profile**: set the
   `p2p-source-producer`'s `prefillProfileName` to the disaggregation
   prefill profile name (default `prefill`), so the source comparison runs
   against the pod that will actually compute the prefix.

Size the decode pool for its NIXL intake - each request ships its full KV
from prefill to decode, and that intake, not prefill placement, is
typically the topology's ceiling.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No pulls, everything serves; EPP logs `bestCachedTokens:0` for every request | index empty: block-size mismatch, missing kv-events, or the kv-events topic port does not match the router's endpoint port (Best Practices, kv-events bullet) - or hashes disagree (`PYTHONHASHSEED`) | verification gates 1 and 4 |
| `rejecting peer connect: block_len mismatch` | `--block-size` differs between pods | align it everywhere |
| No pulls from a TP-mismatched source, index and hashes fine | peer session fingerprint is TP-locked | matched TP; hetero-TP only for non-hybrid models on the V1 runner (Best Practices) |
| Pulls fire but hit rate ~0 | CPU tier too small vs GPU cache; prefixes evicted before peers ask | grow `cpu_bytes_to_use` (and `/dev/shm`) |
| Sidecar exits with `unknown flag: --enable-p2p-pull` | sidecar image predates the NIXL PD pull path | use a sidecar build that includes it |
| Zero pulls after moving to the current connector branch, gates 1-2 pass | sidecar emits the old sub-dict keys (`p2p`/`prefill`/`decode`); the renamed engine ignores them | use a sidecar built with the renamed keys (`remote_kv_source`/`remote_prefiller`/`remote_decoder`) |
| TTFT pins flat at ~the token-producer timeout (default 5s) at every rate above some cliff, engines report near-zero queue/prefill time, both arms identical | render service saturated; every EPP render call times out and requests proceed late without token IDs | scale render replicas to `peak_req_per_s x tokenize seconds per request`; verify with a direct load test against `/v1/completions/render` |

## Benchmarking Reports

Empirical benchmark reports comparing the routing arms under identical
hardware configurations:

- **[openai/gpt-oss-120b on vLLM (H200, aggregated)](./benchmark-results/gpt-oss-120b-h200.md)**:
  pull-versus-recompute crossover, shared-prefix pools, and the document
  Q&A headline - load-aware placement plus the pull against precise
  prefix-cache routing.
- **[Qwen/Qwen3-30B-A3B-Thinking on vLLM (H200, P/D agentic)](./benchmark-results/qwen3-30b-h200-pd-agentic.md)**:
  prefill pulling decode's generated session history on a disaggregated
  deployment - 6.3x median TTFT and +50% throughput against plain NIXL P/D
  on the agentic-serving workload shape.
- **[zai-org/GLM-5.2-FP8 on vLLM (H200, wide-EP P/D)](./benchmark-results/glm-5.2-h200.md)**:
  the mechanism at 753B - crossover swept to its measured tie (13.6K
  tokens), a four-arm placement-x-pull grid on recorded agentic traces,
  and the pull firing from the approximate index as well as the precise
  one.
