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
* the llm-d Router's precise (KV-event-fed) prefix index, and
* the `p2p-source-producer`, which stamps each request with the peer that
  holds the most cached prefix so the routing sidecar can inject
  `kv_transfer_params.p2p` and the engine pulls instead of recomputing.

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
* **Long prefixes.** The pull is a near-constant-time CPU-to-CPU copy while
  recompute grows with length. Measure the crossover for your model (the
  benchmark below does); route pulls only above it.

The placement rule that falls out of every measurement in this guide: the
pull is a recovery path, not a placement strategy. Keep prefix-affinity
placement primary - a local hit is free, and a pod's cache mass compounds
turn over turn - and let the pull cover divergence: queue-pressure spills,
evictions across idle gaps, cold replicas, session migration. Reach for
load-aware placement plus the pull only when the working set oversubscribes
the fleet's GPU caches (wide prefix pools, large-document corpora), where
placing by cache location pays in queues and recomputes. Inverting the rule
- placing purely by load and pulling everywhere - scatters cache mass so no
peer accumulates enough to serve from, and turns the transfer path into
sustained bandwidth paid on every request; it loses to affinity at every
load in our measurements and in an independent wide-EP deployment.

## Configuration

### Router scheduling configurations

Four EPP scheduling configurations ship with the guide (under
[benchmarking/](benchmarking/)). The recommended deployment is
`epp-affinity-p2p.yaml`; the others are the comparison arms the guide's
measurements use:

| Config | Placement | Pull |
|---|---|---|
| [`epp-affinity-p2p.yaml`](benchmarking/epp-affinity-p2p.yaml) | precise prefix-cache affinity | `p2p-source-producer`, `minCachedTokenDelta: 2048` (recommended) |
| [`epp-affinity.yaml`](benchmarking/epp-affinity.yaml) | precise prefix-cache affinity | none (baseline) |
| [`epp-load.yaml`](benchmarking/epp-load.yaml) | load-balanced | none (recompute control) |
| [`epp-load-p2p.yaml`](benchmarking/epp-load-p2p.yaml) | load-balanced | `p2p-source-producer` (oversubscribed working sets) |

`minCachedTokenDelta` is set from the measured pull-versus-recompute
crossover (see [Benchmarking](#benchmarking)): a pull is requested only when
a peer holds at least that many more cached prefix tokens than the scheduled
pod.

### Supported Hardware Backends

* NVIDIA GPU / vLLM (measured on H200; any CUDA GPU with enough HBM for the
  model works). RDMA between pods (`rdma/ib` resources) gives NIXL/UCX
  transfer rates; TCP works functionally at reduced rates.

## Best Practices

Each of these was learned the hard way:

* `--block-size` identical on every pod AND in the router's
  `precise-prefix-cache-producer` (`tokenProcessorConfig.blockSize`). A
  mismatch leaves the prefix index empty and the whole path silently inert -
  requests still serve, nothing pulls.
* `--kv-events-config` on every serving pod, topic
  `kv@<POD_IP>:<PORT>@<model>`. No events, no precise index, no source
  selection.
* `PYTHONHASHSEED` pinned to the same value fleet-wide. vLLM seeds block
  hashes per process; unpinned seeds mean no block hash ever matches across
  pods and every lookup misses.
* `offload_prompt_only: false` - sources must offload computed prefixes,
  not only prompts, for peers to pull them.
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
* llm-d routing sidecar with `kv_transfer_params.p2p` injection.

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
the values for `epp-affinity.yaml` or `epp-load.yaml` from
[benchmarking/](benchmarking/).

```bash
helm upgrade -i ${GUIDE_NAME} llm-d-router \
  -n ${NAMESPACE} \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml
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

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm
```

16 replicas, TP=1, `--block-size=64`, KV events on, the offloading connector
with a P2P tier on port 7777.

#### Interim: P2P tier from the development branch (temporary, will be replaced)

No vLLM release ships the `OffloadingConnector` P2P secondary tier yet; it
lives on the development branch
[`liranschour/vllm@generic_p2p`](https://github.com/liranschour/vllm/tree/generic_p2p).
Until a release includes it, overlay that branch's implementation onto the
image by mounting its source files over the installed package. This whole
subsection - the ConfigMap, `patch-p2p-src.yaml`, and the image pin - is
removed once the tier is released.

1. Clone the branch and create the source ConfigMap (keys are flattened;
   they must match the `subPath` names in `patch-p2p-src.yaml`):

   ```bash
   git clone -b generic_p2p https://github.com/liranschour/vllm.git /tmp/vllm-p2p
   cd /tmp/vllm-p2p
   kubectl create configmap generic-p2p-src -n ${NAMESPACE} \
     --from-file=envs.py=vllm/envs.py \
     --from-file=spec.py=vllm/v1/kv_offload/tiering/spec.py \
     --from-file=manager.py=vllm/v1/kv_offload/tiering/p2p/manager.py \
     --from-file=data_base.py=vllm/v1/kv_offload/tiering/p2p/data/base.py \
     --from-file=data_nixl.py=vllm/v1/kv_offload/tiering/p2p/data/nixl.py \
     --from-file=session_client.py=vllm/v1/kv_offload/tiering/p2p/session/client.py \
     --from-file=session_protocol.py=vllm/v1/kv_offload/tiering/p2p/session/protocol.py \
     --from-file=session_server.py=vllm/v1/kv_offload/tiering/p2p/session/server.py \
     --from-file=session_session.py=vllm/v1/kv_offload/tiering/p2p/session/session.py
   ```

2. Uncomment the `patches:` entry for `patch-p2p-src.yaml` in
   `modelserver/gpu/vllm/kustomization.yaml` and re-apply.

3. Pin the modelserver image to the vLLM nightly the branch is rebased on.
   The mounted files import symbols from the rest of the installed package,
   so image and branch must move together - a drifted pair crashes
   EngineCore at startup (the failure is loud, not silent).

### 4. (Optional) Enable Monitoring

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
    -- curl -X POST http://${IP}/v1/completions \
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
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
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
    --workload workload/profiles/inference-perf/guide_p2p-kv-cache-sharing_1.yaml
```

The full scenario matrix (crossover micro-benchmark, shared-prefix pools,
hot set, document Q&A) with its measured tables and the A/B protocol lives
in [benchmarking/README.md](benchmarking/README.md).

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render
```

## How It Works

1. **Model server pods publish KV-cache events** and run the
   `OffloadingConnector` with a CPU tier plus a P2P secondary tier: every
   pod both offloads its computed KV to CPU and serves it to peers on the
   P2P port.
2. **The router builds the precise prefix index** from the KV events, so it
   knows per request which pods hold which prefix blocks.
3. **The `p2p-source-producer` compares** the best-cached peer against the
   pod scheduling actually picked; when the peer leads by at least
   `minCachedTokenDelta` tokens it sets the KV cache source header.
4. **The routing sidecar injects `kv_transfer_params.p2p`** from the header
   and the engine pulls the prefix blocks from the peer's CPU tier over
   NIXL - hits load as normal cache hits, misses recompute, so a failed
   transfer degrades to baseline behavior instead of failing the request.

## P/D variant: P2P over NIXL disaggregation

Under P/D disaggregation the pull applies to the **prefill leg only**: the
prefill worker computes the prompt KV and streams it to the decoder, so
that is the leg where recomputing a cached prefix is wasted work. The EPP
evaluates the source header against the prefill profile's target, and the
sidecar injects `kv_transfer_params.p2p` onto the prefill leg; the decode
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
| No pulls, everything serves | index empty (block-size mismatch, missing kv-events) or hashes disagree (`PYTHONHASHSEED`) | verification gates 1 and 4 |
| `rejecting peer connect: block_len mismatch` | `--block-size` differs between pods | align it everywhere |
| Pulls fire but hit rate ~0 | CPU tier too small vs GPU cache; prefixes evicted before peers ask | grow `cpu_bytes_to_use` (and `/dev/shm`) |
| Sidecar exits with `unknown flag: --enable-p2p-pull` | sidecar image predates the NIXL PD pull path | use a sidecar build that includes it |
| TTFT pins flat at ~the token-producer timeout (default 5s) at every rate above some cliff, engines report near-zero queue/prefill time, both arms identical | render service saturated; every EPP render call times out and requests proceed late without token IDs | scale render replicas to `peak_req_per_s x tokenize seconds per request`; verify with a direct load test against `/v1/completions/render` |

## Benchmarking Reports

Empirical benchmark reports comparing the three routing arms under
identical hardware configurations:

- **[openai/gpt-oss-120b on vLLM (H200, aggregated)](./benchmark-results/gpt-oss-120b-h200.md)**:
  pull-versus-recompute crossover, shared-prefix pools, and the document
  Q&A headline - load-aware placement plus the pull against precise
  prefix-cache routing.
