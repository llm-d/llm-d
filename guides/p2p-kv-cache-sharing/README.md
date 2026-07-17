# P2P KV Cache Sharing

## Overview

This guide deploys `openai/gpt-oss-120b` with peer-to-peer KV cache sharing:
any vLLM instance can pull cached prefix KV blocks directly from a peer's CPU
offload tier instead of recomputing them. The transfer is CPU-to-CPU over
NIXL (UCX/RDMA when available) - the source pod's GPU is never touched, so
serving a pull costs the source no prefill capacity.

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

Cache-affinity routing remains optimal when the prefix distribution is
uniform and per-pod caches hold their shares - the benchmark's affinity arm
makes that regime visible rather than hiding it.

### Best practices (each of these was learned the hard way)

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
* CPU tier (`cpu_bytes_to_use`) at least as large as the per-pod GPU KV
  cache, ideally 2x. Peers pull from the CPU tier: if it is smaller than
  the GPU cache, the router's view of "who has this prefix" outruns what
  sources can actually serve. Size `/dev/shm` above `cpu_bytes_to_use`
  (the tier is an shm mmap).
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

Same client tooling, namespace, and HF token setup as the
[P/D disaggregation guide](../pd-disaggregation/README.md#prerequisites),
plus:

* A vLLM image with the `OffloadingConnector` P2P secondary tier.
* llm-d routing sidecar with `kv_transfer_params.p2p` injection.
* RDMA between pods (`rdma/ib` resources) for NIXL/UCX transfer rates;
  TCP works functionally at reduced rates.

## Installation

### 1. Deploy the llm-d Router

Standard router install; then apply one of the scheduling configs under
`router/`:

* `epp-affinity.yaml` - precise prefix-cache affinity (no p2p; baseline).
* `epp-load.yaml` - load-balanced placement, no p2p (control).
* `epp-load-p2p.yaml` - load-balanced placement + `p2p-source-producer`
  (the path this guide is about). `minCachedTokenDelta` is set from the
  crossover measurement below.

### 2. Deploy the model server

```bash
kubectl apply -n ${NAMESPACE} -k guides/p2p-kv-cache-sharing/modelserver/gpu/vllm
```

16 replicas, TP=1, `--block-size=64`, KV events on, the offloading connector
with a P2P tier on port 7777.

## Verification (mechanism-engaged gates)

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

See [benchmarking/README.md](benchmarking/README.md): a crossover micro
(sets `minCachedTokenDelta`), a uniform shared-prefix pool (three routing
arms), and a skewed pool (the payoff case), all via the llm-d benchmarking
framework (inference-perf).

## P/D variant

Apply the same three scheduling profiles to the prefill profile of the
[P/D disaggregation guide](../pd-disaggregation/README.md) and run the
routing sidecar with `--enable-p2p-pull` (NIXL PD path): the sidecar then
injects the pull into the prefill leg, and prefill workers pull prefixes
from peers. Size the decode pool for its NIXL intake - each request ships
its full KV from prefill to decode, and that intake, not prefill placement,
is typically the topology's ceiling.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No pulls, everything serves | index empty (block-size mismatch, missing kv-events) or hashes disagree (`PYTHONHASHSEED`) | verification gates 1 and 4 |
| `rejecting peer connect: block_len mismatch` | `--block-size` differs between pods | align it everywhere |
| Pulls fire but hit rate ~0 | CPU tier too small vs GPU cache; prefixes evicted before peers ask | grow `cpu_bytes_to_use` (and `/dev/shm`) |
| Sidecar exits with `unknown flag: --enable-p2p-pull` | sidecar image predates the NIXL PD pull path | use a sidecar build that includes it |
| TTFT pins flat at ~the token-producer timeout (default 5s) at every rate above some cliff, engines report near-zero queue/prefill time, both arms identical | render service saturated; every EPP render call times out and requests proceed late without token IDs | scale render replicas to `peak_req_per_s x tokenize seconds per request`; verify with a direct load test against `/v1/completions/render` |
