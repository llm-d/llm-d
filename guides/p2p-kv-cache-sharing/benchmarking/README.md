# Benchmarking P2P KV cache sharing

All runs use the llm-d benchmarking framework (inference-perf) against the
gateway. Every scenario is preceded by the guide's verification gates; a run
where the mechanism is not provably engaged measures nothing.

## Running the benchmark

The headline scenario ships as a dedicated `llmdbenchmark` workload profile,
the same way the other guides' benchmarks do. As of this writing the
profile lands via
[llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656),
not yet merged - check out that PR's branch until it lands, and pin the
merging commit SHA once it does, so a later run of this command reproduces
the same workload the tables below were measured against, not whatever
`main` happens to be. Install the CLI, resolve your endpoint, and run:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
cd llm-d-benchmark && source .venv/bin/activate
# Until llm-d-benchmark#1656 merges:
git fetch origin feat/p2p-guide-profile && git checkout feat/p2p-guide-profile

export ENDPOINT_URL="http://$(kubectl get service <your-epp-service> -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"

llmdbenchmark \
    --spec guides/p2p-kv-cache-sharing \
    run \
    --endpoint-url "${ENDPOINT_URL}" \
    --workload workload/profiles/inference-perf/guide_p2p-kv-cache-sharing_1.yaml
```

Run the profile once per routing arm, switching only the EPP configuration
between runs. The three arm configs used for the gpt-oss tables below ship
next to this file:

* [`epp-affinity.yaml`](epp-affinity.yaml) - precise prefix-cache routing
  (the precise guide's configuration, complete).
* [`epp-load.yaml`](epp-load.yaml) - load-balanced placement, no pull (the
  recompute control).
* [`epp-load-p2p.yaml`](epp-load-p2p.yaml) - load-balanced placement + the
  P2P pull (`minCachedTokenDelta: 2048`, from the crossover below).

A second arm set, `epp-glm-*.yaml`, is the wide-EP testbed's
(`GLM-5.2-FP8`, 753B): the same placement-x-pull cross at
`minCachedTokenDelta: 16384`, measured in the
[wide-EP section](#wide-ep-testbed-glm-52-fp8) below.

For a defensible A/B, run arm pairs twice with the order alternated:
whichever arm runs second inherits warm CPU tiers, and the alternation both
cancels that advantage and measures each arm's sensitivity to inherited
cache state.

Model: `openai/gpt-oss-120b`, 16x TP=1 H200 (aggregated). Sizing inputs
measured on this rig: ~41.5 KB KV per token, ~1.22M tokens of GPU KV per pod
at `--gpu-memory-utilization=0.85` (from the engine startup log), CPU tier
sized to 88 GiB (~2.22M tokens, ~1.8x the GPU KV cache) so sources can
serve everything their GPU view advertises. Render service
sized for the peak stage rate (see the guide's best practices): one replica
saturates near 10 req/s on ~50K-token prompts and a saturated render stalls
every request for the token-producer timeout, flattening all arms to the
same false plateau; this rig runs 6 replicas (measured: 30 req/s at p50
82 ms direct).

## What each scenario isolates

Two of the scenarios below change placement and the pull together, so their
headline margin is not a P2P margin. Read them for what they are:

| Scenario | Compares | Isolates the pull? |
|---|---|---|
| Step 0 | recompute vs pull, same pod pair, no routing | **yes** |
| Wide-EP (GLM) | `precise` vs `precise + pull`, placement fixed | **yes** |
| Scenario B | `affinity` vs `load + P2P` | no - placement and pull move together |
| Scenario D | `affinity` / `affinity + P2P` / `load + P2P` | partly - the affinity pair isolates it, the winning arm does not |

The two that isolate it are where the feature's value is established:
Step 0 (-49% to -88% TTFT with RDMA) and the wide-EP arm pair (-16% p50 /
-15% p90 at c128, -27% / -45% at c32). Scenarios B and D show what the
resulting *deployment* does, which is the number an operator cares about,
but attribute their wins to the placement change as much as to the pull.

One result worth stating plainly because it recurs: **under affinity
placement the pull is close to inert.** Measured on the Scenario D rig,
`affinity + P2P` established 2 P2P sessions across 16 pods over a full run
while `load + P2P` established 65 on the same rig - affinity keeps the KV
local, so `minCachedTokenDelta` is rarely met and there is nothing to fetch.
That is the pull behaving correctly as a recovery path, not a defect, but it
does mean `affinity + P2P` should be chosen for its placement behaviour and
treated as insurance against cache/placement divergence, not as a throughput
feature. See [When to use this path](../README.md#when-to-use-this-path).

## Step 0 - pull-versus-recompute crossover (single request)

This ladder was measured with `rdma/ib` on the model-server pods, and the
ladder is transport-dependent - so record which transport yours is on
before comparing against it. Without the IB device exposed to the container
NIXL/UCX falls back to TCP, the pull leg inflates while recompute is
unchanged, and the crossover moves from below 2K out to ~29K (measured:
+26.7% / +20.2% / +10.9% / +6.7% / -4.9% / -15.3% at
2K/8K/16K/24K/32K/48K). That is a different `minCachedTokenDelta`, not a
broken feature, but reading this table while running on TCP will mis-set it.
`ls /dev/infiniband` in the container tells you which case you are in. See
[Supported Hardware Backends](../README.md#supported-hardware-backends).

Seed a fresh prefix on one pod; measure single-request prefill latency on a
cold pod with and without the pull, at prefix lengths 2K/8K/16K/32K/48K.
The crossover sets the router's `minCachedTokenDelta`: below it a pull costs
more than recomputing. This measurement is automated as a
[calibration recipe](../../recipes/router/calibration/README.md#calibrating-mincachedtokendelta)
that runs against two live pods and prints the recommended value. Calibrate on a *warmed* pod pair - the first pull
between two peers pays a one-time session-establishment cost (~6 s measured
on the wide-EP testbed) that steady-state pulls never see, so a single cold
probe reads the transient, not the pull.

gpt-oss-120b note: with ~5.1B active parameters recompute is fast
(~29K tokens/s prefill on H200), but the compact hybrid-attention KV
(41.5 KB/token) makes the transfer cheaper still - the pull wins on latency
at every measured length, and additionally removes the prefill work from
the fleet, which the pool scenarios measure directly.

Measured (5-rep medians, warm mesh, unique prefixes per repetition):

| prefix tokens | recompute | P2P pull | TTFT delta |
|---|---|---|---|
| 2,048 | 70.6 ms | 49.0 ms | -31% |
| 8,192 | 205.4 ms | 120.1 ms | -42% |
| 16,384 | 426.3 ms | 196.2 ms | -54% |
| 32,768 | 983.0 ms | 376.3 ms | -62% |
| 49,152 | 1,695 ms | 550.5 ms | -68% |

The pull wins at every measured length - gpt-oss's compact KV (41.5 KB/token)
makes the transfer cheap enough to beat even this model's fast MoE prefill.
`minCachedTokenDelta: 2048` (the smallest measured winning length).

## Scenario A - uniform shared-prefix pool (three routing arms)

128 shared prefixes x 48K tokens (~6M-token working set, ~5x one pod's GPU
cache), 256-token questions, 64 output tokens, streaming, constant-rate
stages ramped past saturation. `load.request_timeout` set explicitly.

Arms (identical workload, identical pods; only the router config changes):

1. `epp-affinity.yaml` - precise prefix-cache affinity. Uniform pools are
   affinity's best case; this arm is the reference ceiling.
2. `epp-load.yaml` - load-balanced placement, no p2p. Every cross-pod
   request recomputes its prefix: the recompute floor.
3. `epp-load-p2p.yaml` - load-balanced placement + pull.

Metrics per arm: achieved vs offered rate, TTFT and request latency
p50/p95, `vllm:external_prefix_cache_hits_total` deltas (pull evidence),
per-pod served counts (placement evidence), restarts (must be 0).

Measured (16x gpt-oss-120b, H200, achieved req/s and latency p50 per stage):

| offered | affinity | affinity + P2P (recommended) | load, no P2P | load + P2P |
|---|---|---|---|---|
| 4 req/s | 3.8 / 2.4s | 3.9 / 2.4s | 3.8 / 4.2s | 3.9 / 2.4s |
| 8 req/s | 7.9 / 0.7s | 7.9 / 0.73s | 6.7 / 6.5s | 7.7 / 1.6s |
| 12 req/s | 11.9 / 0.7s | 11.9 / 0.75s | 9.0 / 24.9s | 11.4 / 2.3s |
| 16 req/s | 15.8 / 0.7s | 15.8 / 0.76s | 8.8 / 37.3s | 15.1 / 3.6s |
| 20 req/s | 19.8 / 0.7s | 19.8 / 0.77s | 9.4 / 48.8s | 15.4 / 15.6s |
| 24 req/s | 23.7 / 0.8s | 23.6 / 0.82s | 9.4 / 63.5s | 16.7 / 30.0s |

Reading the arms: affinity is near-ideal on a uniform pool - each pod owns
~8 of the 128 prefixes (384K tokens, comfortably GPU-resident), so with a
working prefix index every request is a local hit; zero failures, flat
sub-second p50. Affinity + P2P matches this ceiling within measurement
noise at every offered rate, tracking offered to saturation at 24 req/s -
the pull mostly sits idle here (placement rarely diverges from cache), so
it costs nothing to have it available for whichever requests do miss. The
recompute control saturates near 9.4 req/s: every cross-pod placement
re-prefills 48K tokens. Load + P2P recovers most of that penalty at
moderate rates (2.3s vs 24.9s p50 at 12 req/s) but degrades sharply at the
top of the ladder - p50 30.0s at 24 req/s, achieving only 16.7 of the 24
offered - load-aware placement scatters each prefix's traffic across more
pods than affinity does, so at saturation there is more pull/transfer
contention to pay than there is queueing to save; affinity + P2P has no
such penalty because it never scatters a prefix's traffic in the first
place. Zero failures and zero restarts in all four arms. Uniform pools are
where affinity + P2P wins outright; the hot-set scenario below and the
document-Q&A headline are where load-aware + P2P's spreading matters
more - see the [placement rule](../README.md#when-to-use-this-path) for
when each applies.

## Scenario B - hot set (the payoff case)

A small hot set takes all traffic: 8 shared prefixes x 48K tokens,
decode-heavy requests (512 output tokens), rates ramped well past what the
prefix owners alone can absorb. Affinity concentrates each hot prefix's
work on its owner pod; load-aware placement plus the pull serves the same
hot content from the whole fleet - each pod pulls the prefix once, then
everything is a local hit. This is the regime the feature exists for:
horizontal scaling of hot content without recomputing it anywhere.

Two capacity facts shape the design; check both on your own rig before
copying it:

* Cache capacity does not differentiate the arms at this model size: a
  hot set small enough for affinity to concentrate (prefix count well
  below pod count) always fits in one pod's GPU KV (8 x 48K = 384K tokens
  vs ~1.22M per pod), so every arm serves GPU hits once warm. The
  differentiator is decode-load concentration on the owners, which is why
  outputs are long and rates high.
* At short outputs and moderate rates (up to 24 req/s measured), the
  owners are nowhere near saturation and the arms are equivalent:
  affinity holds ~0.33s TTFT p50 flat with zero failures. A hot-set
  scenario that shows no difference at low load is measuring headroom,
  not a pathology.

Measured (16x gpt-oss-120b, H200; 8 x 48K prefixes, 512-token outputs,
achieved req/s, latency p50, failures per stage):

| offered | affinity (8 owner pods) | load + P2P (16 pods) |
|---|---|---|
| 12 req/s | 9.9 / 11.8s / 0 | 11.3 / 5.6s / 0 |
| 24 req/s | 14.7 / 27.0s / 0 | 20.9 / 9.6s / 0 |
| 36 req/s | 15.3 / 53.8s / 0 | 29.1 / 15.9s / 0 |
| 48 req/s | 13.1 / 75.3s / 672 | 34.3 / 26.0s / 0 |

The owner pods cap near 15 req/s aggregate and shed 672 requests to the
120s client timeout at offered 48; load+P2P takes 2.6x the throughput at
roughly one-third the latency with zero failures. Pull evidence: ~5.9M
external prefix-hit tokens - each pod pulls each hot prefix once, then
every request is a local hit. TTFT stays under 1s p50 in both arms; the
collapse is pure decode concentration on the owners, which is the
pathology the pull relieves.

## Scenario D - document Q&A (the headline; shipped as the profile above)

The user-facing regime: each of 192 conversations carries a private
48K-token document prefix and asks 6 short questions (256-token answers),
128 conversations concurrent. Per-turn decode is small, so TTFT dominates
the experience. With ~9.2M tokens of document prefix spread across the
fleet and 128 sessions concurrently active against a fixed
per-document ownership rule, request placement - not aggregate GPU
capacity - decides whether a question is a cache hit, a 48K recompute, or
a wait behind someone else's document. This is the scenario
`guide_p2p-kv-cache-sharing_1.yaml` reproduces; scale `num_conversations`
and `concurrency` relative to your fleet's pod count so enough sessions
contend for a limited set of owner pods.

Three arms measured; `epp-affinity` and `epp-load-p2p` as two full runs
with order alternated, `epp-affinity-p2p` as two independent runs (order
alternation does not apply with only one non-baseline arm running). All
six runs completed 1,152/1,152 turns with zero errors and zero restarts.
TTFT p50/p95/p99 (s) and throughput (turns/s):

| run | `epp-affinity` (precise routing) | `epp-affinity-p2p` (recommended default) | `epp-load-p2p` (wins this scenario) |
|---|---|---|---|
| 1 | 4.1 / 41.0 / 80.5; 5.98 | 4.0 / 27.7 / 48.8; 5.6 | 4.5 / 13.0 / 20.9; 7.02 |
| 2 (order reversed / 2nd run) | 4.2 / 17.3 / 37.2; 7.66 | 2.6 / 33.6 / 67.2; 5.7 | 3.9 / 12.5 / 26.7; 7.76 |

`epp-affinity-p2p`'s throughput figures (5.6-5.7 turns/s) undercount
slightly: a sub-1% tail of requests generated unusually long reasoning
output under `ignore_eos: true` (median/p90/p95 output length all land on
the intended 256-token target - only `p99.9` blows out), stretching wall
time. TTFT is unaffected, since it is measured at first token, before that
generation happens.

`epp-affinity-p2p` sits between the other two arms on tail latency in both
runs, not just one: its p95/p99 (27.7-33.6s / 48.8-67.2s) improve on
`epp-affinity`'s worst case (80.5s p99) but do not reach `epp-load-p2p`'s
range (12.5-13.0s p95 / 20.9-26.7s p99) in either run. The likely reason:
`epp-affinity-p2p` still queues a request behind a busy owner pod whenever
the scorer's affinity term outweighs load, and only pulls once a peer
already out-caches the scheduled pod - it recovers the *cache-locality*
cost of a cross-pod placement but not the *queueing* cost of a placement
decision that still prefers a busy owner. `epp-load-p2p` never makes that
tradeoff: placement always goes to the least-loaded pod, so there is no
owner-pod queue to build in the first place. On this scenario alone,
`epp-load-p2p` is the better arm - but see
[Scenario A](#scenario-a---uniform-shared-prefix-pool-three-routing-arms), where
the result is reversed. The guide ships `epp-affinity-p2p` as the default
because it is the safer general-purpose choice across both regimes (see
the [README](../README.md#when-to-use-this-path)); reach for
`epp-load-p2p` specifically when your workload looks like this one -
many concurrent, multi-turn sessions each pinned to an owner pod.

Medians are equal - a home session answers from warm cache either way. The
arms separate on tails and stability: p99 TTFT 21-27s versus 37-81s (2-4x),
up to +17% throughput, and 10% run-to-run spread versus 28%. Pull evidence:
30-32M tokens moved between pods per P2P-arm run; the affinity arm did
23-31M local CPU-tier restores instead. Prefix-first placement queues
displaced questions behind a concentrated owner's 48K prefills; load-aware
placement plus the pull converts each displacement into a ~0.6s transfer.

## Wide-EP testbed (GLM-5.2-FP8)

The mechanism at the other end of the scale: `zai-org/GLM-5.2-FP8` (753B
MoE), one prefill + one decode instance, each 16-way data/expert-parallel
across 2 pods (32x H200). The workload replays recorded agentic traces (the
SemiAnalysis Weka corpus) with aiperf at concurrencies 32/64/128; the four
`epp-glm-*.yaml` arms cross the prefix-affinity index (precise vs
approximate) with the pull on or off at `minCachedTokenDelta: 16384` - the
measured crossover for this model (dead tie at 13,648 tokens; the pull is
~1.7-2.3 s nearly flat while recompute pays ~130-144 us/token, reaching
-83% at 98K).

Headline cell (concurrency 32): adding the pull to precise affinity takes
TTFT p50 -27% and p90 -45%, tying the best load-balanced arm - the pull
erases affinity's concentration penalty. The approximate arm drove pulls
from the prompt-hash index alone (no KV events), confirming the source
decision works with either index. Full grid, crossover sweep, and
per-cell pull evidence:
[../benchmark-results/glm-5.2-h200.md](../benchmark-results/glm-5.2-h200.md).

## Run hygiene

* Compare each stage's wall-clock to send-window + drain; a stretched wall
  with fast successes means hung requests, not slow serving.
* `report.request_lifecycle.per_request: true` - per-request records make
  hangs and tails attributable.
* Record pull evidence per arm; a p2p arm with zero external hits is a
  misconfigured control, not a result.
* Diff engine-side timing sums (`vllm:request_queue_time_seconds`,
  `vllm:request_prefill_time_seconds`, `vllm:time_to_first_token_seconds`)
  across each stage and reconcile them with client-observed TTFT. Client
  latency the engines never saw lives in the gateway path (router, render,
  sidecar), not the serving fleet.
* Run a low-rate independent probe through the gateway during stages. A
  latency plateau that is flat across offered rates and identical across
  arms is a fixed timeout somewhere in the path, not saturation - queueing
  grows with rate; timeouts do not.
