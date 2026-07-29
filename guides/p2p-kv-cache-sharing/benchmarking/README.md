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

Measured (16x gpt-oss-120b, H200, `rdma/ib` on every pod; achieved req/s /
TTFT p50 / request latency p50 per stage):

| offered | affinity | load, no P2P | load + P2P |
|---|---|---|---|
| 6 req/s | 5.97 / 207 ms / 0.50 s | 5.59 / 2.5 s / 5.6 s | 5.96 / 342 ms / 0.64 s |
| 12 req/s | 11.92 / 200 ms / 0.49 s | 9.02 / 8.6 s / 26.2 s | 11.49 / 460 ms / 0.98 s |
| 18 req/s | 17.87 / 192 ms / 0.48 s | 8.58 / 26.0 s / 45.7 s | 17.46 / 341 ms / 0.67 s |
| 24 req/s | 23.82 / 191 ms / 0.48 s | 9.01 / 43.8 s / 63.4 s | 21.93 / 344 ms / 0.70 s |
| 30 req/s | 29.76 / 184 ms / 0.48 s | 9.21 / 61.3 s / 81.2 s | 29.19 / 342 ms / 0.73 s |

Zero failures and zero restarts in all arms (16,200 requests). Pull
evidence in the `load + P2P` arm: 120 P2P sessions, 210M external-hit
tokens, 7.8 TB served from the offload tier (GPU hit rate 17.3% - scattered
placement misses locally and the tier covers it).

Reading the arms: affinity is near-ideal on a uniform pool - each pod owns
~8 of the 128 prefixes (384K tokens, comfortably GPU-resident), so with a
working prefix index every request is a local hit; flat sub-half-second p50
through 30 req/s. `affinity + P2P` matches this ceiling within noise (the
pull is idle under affinity placement - see
[What each scenario isolates](#what-each-scenario-isolates)). The recompute
control saturates near 9 req/s: every cross-pod placement re-prefills 48K
tokens. **The pull sets load placement's floor**: `load + P2P` tracks
offered rate through 30 req/s at sub-second p50 - against the recompute
floor at rate 24 that is 9.01 -> 21.93 req/s (+143%) and 63.4 s -> 0.70 s
p50; at rate 30, +217%. Affinity remains the better arm on this workload
(0.48 s vs 0.73 s p50, slightly higher achieved) because scattering pays
transfer work affinity never pays - but the gap is a constant factor, not a
collapse. Uniform pools are where affinity-style placement wins; the
document-Q&A headline is where load-aware + P2P's spreading matters more -
see the [placement rule](../README.md#when-to-use-this-path) for when each
applies.

## Scenario B - hot set (the payoff case)

A small hot set takes all traffic, decode-heavy requests (512 output
tokens), rates ramped past what the prefix owners alone can absorb.
Affinity concentrates each hot prefix's work on its owner pod; load-aware
placement plus the pull serves the same hot content from the whole fleet.

**Size the hot set against one pod's GPU KV capacity before running this -
that ratio decides the result, and nothing else about the scenario
matters if it is wrong.** Measured on 16x gpt-oss-120b (~1.22M tokens of
GPU KV per pod), walking 48K-token prefixes:

| hot set | vs one pod's cache | what happens |
|---|---|---|
| 8 prefixes (384K tok) | 0.31x | fits in every pod - after warmup every arm serves GPU hits, nothing is recomputed and the pull never fires. Measures headroom, not a pathology. |
| 32 prefixes (1.54M tok) | 1.26x | one stage of churn while placement redistributes, then replication absorbs it and the arms converge |
| **64 prefixes (3.07M tok)** | **2.5x** | **misses are permanent; this is the regime the scenario is about** |

Measured at 64 x 48K (achieved req/s / TTFT p50 / request latency p50):

| offered | `affinity` | `load` - no P2P | `load + P2P` |
|---|---|---|---|
| 12 req/s | 11.94 / 188 ms / 0.30 s | 9.31 / 7.9 s / 16.6 s | 11.84 / 310 ms / 0.42 s |
| 24 req/s | 23.04 / 183 ms / 0.31 s | 11.47 / 24.3 s / 34.5 s | 22.83 / 271 ms / 0.42 s |
| 36 req/s | 34.03 / 190 ms / 0.36 s | 11.77 / 47.0 s / 61.6 s | 34.34 / 249 ms / 0.45 s |
| 48 req/s | 46.03 / 196 ms / 0.38 s | 13.85 / 58.2 s / 72.5 s, **274 failures** | 44.93 / 254 ms / 0.48 s, **0 failures** |

Pull evidence: 120 P2P sessions, 204M external-hit tokens, 7.5 TB served
from the offload tier, GPU hit rate 43.2% (the set genuinely does not fit).

**The pull is the difference between a serving fleet and a shedding one.**
Same placement, pull as the only variable, at offered 48: 13.85 -> 44.93
req/s (+224%), TTFT p50 58.2 s -> 254 ms, and 274 client-timeout failures
-> zero. The recompute floor caps near 12-14 req/s at every rate above 24 -
each displaced request re-prefills 48K tokens - while the pull arm tracks
offered rate to 48 within 2% of affinity's throughput.

Affinity is not the arm that suffers here: with 64 prefixes over 16 pods
ownership spreads ~4 per pod, no owner is overloaded, and affinity holds
46 req/s at 196 ms. Owner concentration is a *separate* pathology that
needs a prefix count well below the pod count; at that count the set also
fits everywhere, so the two effects are hard to exhibit in one workload.
Choose which one you are testing and size accordingly.

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
