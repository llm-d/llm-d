# Remaining-work routing (GLM) [Experimental]

Routes cached long-context follow-ups by their remaining prefill work and
pulls the transferable prefix over the P2P tier. The router feature is
[llm-d-router#2559](https://github.com/llm-d/llm-d-router/pull/2559)
(design: [llm-d-router#2540](https://github.com/llm-d/llm-d-router/issues/2540));
until it merges and ships, this guide pins experimental images built from
that branch.

Measured results and the exact configuration behind them:
[benchmark-results/remaining-work-routing](../benchmark-results/remaining-work-routing/RESULTS.md).

## Images

- vLLM: `quay.io/niliguy/vllm-openai:nightly-6f91edf9-pr50302` - the
  2026-07-29 nightly plus [vllm#50302](https://github.com/vllm-project/vllm/pull/50302)
  ("Universally align block table width to 128 tokens"). A self-built vLLM
  works if it satisfies the requirements below; this image is the reference.
- EPP: `quay.io/niliguy/llm-d-router-endpoint-picker:p2p-cache-routing-709e0991` -
  build of [`nilig/llm-d-router` branch `feat/p2p-cache-aware-context-routing`](https://github.com/llm-d/llm-d-router/compare/main...nilig:llm-d-router:feat/p2p-cache-aware-context-routing)
  at `709e0991`. This commit matters: it carries the confirmed-cache
  accounting and the tier-gated reusable floor; older builds break the
  routing invariant under speculative indexing or approximate producers.
- Decode sidecar: `quay.io/niliguy/llm-d-router-disagg-sidecar:kv-source-endpoint-92e5de82`.

All amd64. Digests are pinned in [engines](engines/) and
[control-plane](control-plane/).

## Deploy

Everything needed is in this directory; nothing else is referenced:

- Full `vllm serve` commands, including the `kv_transfer_config` JSON for the
  `MultiConnector` (NixlConnector + TieringOffloadingSpec with the p2p tier):
  [engines/prefill-runtime.json](engines/prefill-runtime.json) for both
  prefillers, [engines/decode-runtime.json](engines/decode-runtime.json) for
  decode. `OFFLOADING_MODE` selects the connector stack; the deployed mode is
  `p2p-tiered`.
- Pod specs, env knobs (`KV_OFFLOAD_CPU_BYTES`, `DP_SIZE_LOCAL`, probes,
  volumes, `rdma/ib`): [engines/base](engines/base/) with per-class patches in
  [engines/p-long](engines/p-long/), [engines/p-short](engines/p-short/), and
  [engines/decode](engines/decode/).
- EPP deployment, both router plugin configs, Envoy, pools, RBAC:
  [control-plane](control-plane/).

Render with the load restrictor off (the kustomizations patch base manifests
by relative path):

```bash
kustomize build --load-restrictor LoadRestrictionsNone engines | kubectl apply -f -
kustomize build --load-restrictor LoadRestrictionsNone control-plane | kubectl apply -f -
```

Per-cluster edits before applying: the `namespace` in every
`kustomization.yaml`, the `kubernetes.io/hostname` affinity pins in
[engines/p-long](engines/p-long/kustomization.yaml)/[p-short](engines/p-short/kustomization.yaml)/[decode](engines/decode/kustomization.yaml),
the HF token secret name, and `imagePullSecrets`. The `glm-5-2-render`
Service (in [engines/services.yaml](engines/services.yaml)) must resolve for
the EPP's `token-producer`; it is a podless Service over the prefill pods'
vLLM ports.

Bringing your own vLLM: any build from upstream main at or after 2026-07-31
(the [vllm#50302](https://github.com/vllm-project/vllm/pull/50302) merge)
qualifies; earlier builds must cherry-pick that block-table alignment fix or
the block-size-64 cross-engine transfers are misaligned. The build must
include `NixlConnector`, `MultiConnector`, `OffloadingConnector` with
`TieringOffloadingSpec` and the `p2p` secondary tier type,
`kv_load_failure_policy`, and the ZMQ `--kv-events-config` publisher - all
present in upstream main in that window; the smokes under
"Verify before benchmarking" prove the stack end to end before any
benchmark. The EPP and sidecar images above are public; the reference vLLM
image is private (ask for pull access only if not building your own).

## Engine requirements
Cold-start: the manifests load weights with `--load-format runai_streamer`
(distributed loader, concurrency 16) and keep weight and compile caches on
node-local hostPath volumes (`/mnt/local/hf-cache`, `/mnt/local/jit-cache`,
with `VLLM_CACHE_ROOT` inside the latter). Keep both when adapting: without
them a GLM-5.2 boot is ~20 minutes instead of ~8-10, and the benchmark
harness restarts every engine once per run. The caches are per node, so the
first boot on a never-used node still pays the full cold cost - budget one
~20-minute boot when adding a node, then it stays warm.


The [engines](engines/) kustomizations (base LWS manifests under
[engines/base](engines/base/)) encode all of these; the list is what to
preserve when adapting:

- Three engines: P-long, P-short, decode. Every pod requests `rdma/ib: 1`;
  without RDMA the 5.6 GB prefix pulls hit the 30 s connector timeout and
  fall back to full recompute (fail-open, but no benefit).
- Identical `--block-size 64` and `PYTHONHASHSEED` across all peers.
- `OFFLOADING_MODE=p2p-tiered` with `offload_prompt_only: false` on both
  prefillers; `KV_OFFLOAD_CPU_BYTES` at most 64 GiB per rank - 96 GiB and
  128 GiB tiers crash UCX memory registration on the CoreWeave image/UCX
  stack (`mlx5dv_devx_obj_modify` Remote I/O then a segfault in
  `ucp_ep_rkey_unpack`).
- KV events publishing per rank (the precise index feeds both the affinity
  gate and the reusable floor).
- Labels per prefiller class, with the work-range ceiling equal to the
  deployed `--max-model-len`:
  - P-short: `llm-d.ai/prefill-work-range: 0-8192`
  - P-long: `llm-d.ai/prefill-work-range: 8193-120000`
  - both: `llm-d.ai/context-length-range: 0-120000`
- Restart discipline: restart P-long, P-short, and decode together, always.
  A solo restart leaves surviving peers with stale NIXL/UCX endpoints
  (vLLM #49238 class) and produces intermittent transfer failures that look
  like transport flakiness. See [nixl-stability-20260825.md](nixl-stability-20260825.md).

## Router configuration

[control-plane/router-config.yaml](control-plane/router-config.yaml) holds
two complete EPP configs; `candidate.yaml` is the remaining-work
configuration. The composition, with the parts that need per-deployment
attention:

```yaml
- type: precise-prefix-cache-producer
  name: precise-cache            # explicit name; see the wiring trap below
- type: p2p-source-producer
  name: p2p-cache-source
  parameters:
    prefixMatchInfoProducerName: precise-cache   # NEVER omit: omitting
                                 # silently auto-wires the approximate
                                 # producer and the floor is built from
                                 # routing history instead of confirmed
                                 # CPU-tier blocks
    minCachedTokenDelta: 1       # the work-range boundary absorbs D-1, so
                                 # raising this needs a wider P-short range
- type: context-length-aware
  name: prefill-work-router
  parameters:
    label: llm-d.ai/prefill-work-range   # a dedicated label; the default
                                 # context-length label is rejected by the
                                 # factory when subtraction is on
    enableFiltering: false       # score-only; the affinity filter is the gate
    reusableTokensProducerName: p2p-cache-source
- type: prefix-cache-affinity-filter
  parameters:
    prefixMatchInfoProducerName: precise-cache
    affinityThreshold: 0.35      # must sit below cpuWeight x cachedFraction;
                                 # with CPU tier weight 0.4 and a full cached
                                 # prefix (~0.99 of prompt) the score is
                                 # ~0.396, so 0.35 holds sticky at idle
    peakPrefillThroughput: 3585  # CALIBRATE: tokens/s of one rank on one
                                 # long prefill (GLM-5.2-FP8 DP8 H200: 3585)
    maxTTFTPenaltyMs: 3500       # CALIBRATE: ~ the measured migration cost
- type: session-affinity-filter  # decode profile: pins a session's decode
  parameters:                    # rank so follow-up P->D transfers are
    sessionIdConfig:             # delta-only instead of full-context
      sources:
      - header: x-session-id
      - header: x-correlation-id
      evictionTtlSeconds: 3600
```

`baseline.yaml` is identical minus `reusableTokensProducerName` - that one
line is the entire A/B difference.

Calibration procedure:

1. `peakPrefillThroughput`: send one cold 100K-token request to a single
   rank; divide prompt tokens by TTFT.
2. `maxTTFTPenaltyMs`: run the idle migration smoke twice (once with the
   gate effectively off via a huge value, once normally); the TTFT delta of
   the migrated follow-up vs the sticky follow-up is the migration cost. Set
   the budget just above it.
3. Work-range boundary: 8,192 is robust for agentic workloads (eligible
   share is flat from 2K to 24K boundaries on the AgentX Weka corpus); set
   the ceiling to your `--max-model-len`.

## Verify before benchmarking

Deploy order: engines, wait Ready, then control-plane. Then gate on exact
counters, not on absence of errors ([workload](workload/) has the harness):

1. Idle session smoke (`run-smoke.sh <arm> <salt>`): follow-up stays on its
   seed P-long rank, `narrowed to sticky` in the EPP log, no P-short
   activity.
2. Migration smoke under load (env per [workload/README.md](workload/README.md)):
   `TTFT load gate broken` in the EPP log, and P-short counters exactly
   `external hit/transfer = seed tokens rounded down to a block multiple`,
   `local compute = new tokens rounded up`. On GLM with a 102,400-token seed
   and 1,024 new tokens: 102,336 and 1,088. Inexact counters mean a
   misconfiguration, not noise.
3. Only then run sustained comparisons (`run-abba.sh`). The runner restarts
   all engines jointly per run and validates session bindings, gate
   activity, and zero measured-phase request errors.

## Known limits

- Native short requests queue behind migrated load when P-short is a single
  engine (measured 1.4 s to 4.3 s p50 on the C64 block); size P-short for
  the migrated volume or expect that regression.
- Source CPU tier capped at 64 GiB per rank on the CoreWeave UCX stack.
- GLM-5.2 streams tokens in `delta.reasoning`; a reasoning-only turn can be
  classified as an empty response by strict clients (AIPerf counts it a
  request error - scope such validation to the measured phase).
