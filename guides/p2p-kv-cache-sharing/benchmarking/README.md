# Benchmarking P2P KV cache sharing

All runs use the llm-d benchmarking framework (inference-perf) against the
gateway. Every scenario is preceded by the guide's verification gates; a run
where the mechanism is not provably engaged measures nothing.

## Running the benchmark

The headline scenario ships as a dedicated `llmdbenchmark` workload profile,
the same way the other guides' benchmarks do. The profile lands via
[llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656),
which is open, so `guide_p2p-kv-cache-sharing_1.yaml` is absent from `main`
and the PR branch lives on a fork rather than on `origin`. Until it merges,
check out the commit the tables below were measured against; replace the
fetch with the merge commit on `main` once it lands, so a later run
reproduces this workload rather than whatever `main` happens to be. Install
the CLI, resolve your endpoint, and run:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
cd llm-d-benchmark && source .venv/bin/activate
# Until llm-d-benchmark#1656 merges the profile comes from the PR fork at the
# pinned commit - `origin` has neither the branch nor the file.
git fetch https://github.com/nilig/llm-d-benchmark.git \
    20ef7570809c9f4935e2015a73537bd77cdafbe3
git checkout 20ef7570809c9f4935e2015a73537bd77cdafbe3

export ENDPOINT_URL="http://$(kubectl get service <your-epp-service> -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"

llmdbenchmark \
    --spec           guides/p2p-kv-cache-sharing \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_p2p-kv-cache-sharing_1.yaml \
    --analyze
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

A second arm set is the wide-EP testbed's (`GLM-5.2-FP8`, 753B):
`epp-glm-precise{,-p2p}.yaml` at `minCachedTokenDelta: 16384`, measured
in the [wide-EP section](#wide-ep-testbed-glm-52-fp8) below, and the
`epp-glm-loadfirst{,-p2p}.yaml` pair - the testbed's load-spill payoff
benchmark (queue weight 3 over affinity weight 1, with and without
`p2p-source-producer`), the matched pair behind the -67% TTFT / 2.7x
result in the GLM results page.

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

| Scenario | The pull-isolating pair | Isolates the pull? |
|---|---|---|
| Step 0 | recompute vs pull, same pod pair, no routing | **yes** |
| Wide-EP (GLM) | `precise` vs `precise + pull` | **yes** |
| Uniform pool | `load` vs `load + P2P` | **yes** |
| Hot set | `load` vs `load + P2P` | **yes** |
| Document Q&A | `affinity` vs `affinity + P2P` | partly - that pair isolates it, but the winning arm (`load + P2P`) also changes placement |

Every scenario except the document-Q&A headline now carries a control arm
with identical placement and no pull, so its margin is the pull's alone.
Comparisons *across* placement policies (`affinity` vs `load + P2P`) answer
a different question - which deployment to run - and should not be read as
P2P deltas.

The isolating pairs are where the feature's value is established: Step 0
(-56% to -88% TTFT with RDMA), the uniform pool (+143% sustained rate at 24
req/s) and the hot set (+224% and 274 client-timeout failures eliminated at
48 req/s). The wide-EP precise pair is a mechanism-verified null on the
fully-fixed stack - every sampled source evaluation ties at delta 0, the
placement rule holding exactly (see the wide-EP section). The cross-placement comparisons show what the resulting
*deployment* does - the number an operator ultimately cares about - but
attribute their margin to the placement change as much as to the pull.

One result worth stating plainly because it recurs: **under affinity
placement the pull is close to inert.** Measured on the document-Q&A rig,
`affinity + P2P` established 2 P2P sessions across 16 pods over a full run
while `load + P2P` established 65 on the same rig - affinity keeps the KV
local, so `minCachedTokenDelta` is rarely met and there is nothing to fetch.
That is the pull behaving correctly as a recovery path, not a defect, but it
does mean `affinity + P2P` should be chosen for its placement behaviour and
treated as a fallback for externally inherited divergence (a cold engine
replica behind an intact router - plausible and unmeasured), not as a
throughput feature or router-restart insurance. See [When to use this path](../README.md#when-to-use-this-path).

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

## Uniform shared-prefix pool (three routing arms)

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
p50/p95, established P2P session counts (pull evidence),
`vllm:external_prefix_cache_hits_total` deltas (offload-tier activity, which
is not the same thing - see below), per-pod served counts (placement
evidence), restarts (must be 0).

Measured (16x gpt-oss-120b, H200, `rdma/ib` on every pod; achieved req/s /
TTFT p50 / request latency p50 per stage):

| offered | affinity | load, no P2P | load + P2P |
|---|---|---|---|
| 6 req/s | 5.97 / 207 ms / 0.50 s | 5.59 / 2.5 s / 5.6 s | 5.96 / 342 ms / 0.64 s |
| 12 req/s | 11.92 / 200 ms / 0.49 s | 9.02 / 8.6 s / 26.2 s | 11.49 / 460 ms / 0.98 s |
| 18 req/s | 17.87 / 192 ms / 0.48 s | 8.58 / 26.0 s / 45.7 s | 17.46 / 341 ms / 0.67 s |
| 24 req/s | 23.82 / 191 ms / 0.48 s | 9.01 / 43.8 s / 63.4 s | 21.93 / 344 ms / 0.70 s |
| 30 req/s | 29.76 / 184 ms / 0.48 s | 9.21 / 61.3 s / 81.2 s | 29.19 / 342 ms / 0.73 s |

Zero failures and zero restarts in all arms (16,200 requests). Pull evidence
in the `load + P2P` arm: **120 established P2P sessions**, against 0 in the
arms without the producer - that is what shows the path engaged.

Alongside it the tier served 210M external-hit tokens and 7.8 TB (GPU hit
rate 17.3% - scattered placement misses locally and the tier covers it).
Read those two as **offload-tier activity, not pull volume**:
`vllm:external_prefix_cache_hits_total` and `kv_offload_load_bytes_total`
count every restore into GPU, including a pod reloading from its own CPU
tier, so they cannot be attributed to peer transfers on a workload with
repeated prefixes. Session counts prove the path engaged but are reusable
peer connections and do not measure request or byte volume either. To
attribute bytes to a peer the consumer must hold no local copy - which is
what the [calibration
recipe](../../recipes/router/calibration/calibrate-min-cached-token-delta.sh)
arranges with fresh token IDs and a no-pull control, and why its byte column
is trustworthy where these fleet-level counters are not.

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

## Hot set larger than one pod's cache

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

## Document Q&A at session scale (the headline; shipped as the profile above)

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

Results are canonical in
[the gpt-oss results page](../benchmark-results/gpt-oss-120b-h200.md#document-qa-the-headline),
measured on the fixed stack with a per-arm cold roll so arms cannot
contaminate each other. Headline: load-aware + P2P wins this scenario
decisively (7.3x better p99 TTFT and +62% throughput than precise routing
warm; 8.0x cold), while precise + P2P is not distinguishable from precise
alone (2 P2P sessions across 16 pods for the whole run versus 65 under
load-aware placement - affinity keeps KV local, so the source delta is
essentially never met), and the affinity arms are cold-start fragile
(47-48 client timeouts on a cold fleet as placement collapses onto one
pod). An earlier 14-pod run without the per-arm cold roll reported a
narrower separation; the fixed-stack rerun supersedes it.

On this scenario alone, `epp-load-p2p` is the better arm - but see
[the uniform pool](#uniform-shared-prefix-pool-three-routing-arms), where
the result is reversed. The guide ships `epp-affinity-p2p` as the default
because it is the safer general-purpose choice across both regimes (see
the [README](../README.md#when-to-use-this-path)); reach for
`epp-load-p2p` specifically when your workload looks like this one -
many concurrent, multi-turn sessions each pinned to an owner pod.

## Wide-EP testbed (GLM-5.2-FP8)

The mechanism at the other end of the scale: `zai-org/GLM-5.2-FP8` (753B
MoE), one prefill + one decode instance, each 16-way data/expert-parallel
across 2 pods (32x H200). The workload replays recorded agentic traces (the
SemiAnalysis Weka corpus) with aiperf at concurrencies 32/64/128; the shipped
`epp-glm-*.yaml` arms are the precise pair (pull on or off) and the
load-first pair; the historical grid additionally crossed in an
approximate-index pair, retained only in the quarantined results-page
record and not shipped. `minCachedTokenDelta: 16384` was the
overlay-era crossover (dead tie at 13,648 tokens); on the upstream tier the
pull floor fell to ~1.25 s and the tie moved to ~8.7K tokens, so new
deployments should set 12,288 - the calibration recipe measures it, and its
paired no-pull control (0.0 MB moved without the parameter, 1,138.2 MB with
it at 12K) is what makes the measurement trustworthy.

Measured on the fully-fixed stack (upstream vLLM tier, rank-aware source
addressing, the router's prefix index sized to the rank-endpoint count),
the precise pair is a **mechanism-verified null**: live sampling captured
115 source evaluations and every one ties at a cached-token delta of
exactly 0 (52 are self-matches), so no `minCachedTokenDelta` fires and the
arms behave identically - medians agree within 1% and the TTFT tail
spread across three runs (p99 17.7-24.6 s) is run-to-run variance. That is
the placement rule holding exactly at 753B: a consistent index under
precise affinity leaves the pull nothing to repair. The overlay-era wins
were real transfers triggered by index-eviction divergence that the
index sizing fix has since removed; that ladder is quarantined as a
historical reproduction record in the results page. The pull's measured
territory on this testbed is load-first placement - the matched
`epp-glm-loadfirst{,-p2p}` pair (-67% mean TTFT, 2.7x throughput).
A cold engine replica behind an intact router is a plausible further
case and is unmeasured; a restarted ROUTER is not one (both index modes
lose the pre-restart cache map, and measured restart-recovery runs
produced zero pulls). Full tables, crossover sweep, and the quarantined
overlay-era grid:
[../benchmark-results/glm-5.2-h200.md](../benchmark-results/glm-5.2-h200.md).

## Run hygiene

* Compare each stage's wall-clock to send-window + drain; a stretched wall
  with fast successes means hung requests, not slow serving.
* `report.request_lifecycle.per_request: true` - per-request records make
  hangs and tails attributable.
* Record pull evidence per arm. For a scenario preregistered to pull
  (load-first placement, fresh-source seeding), zero engagement means a
  misconfigured run; for an affinity arm a mechanism-verified zero is a
  legitimate null (the fixed GLM precise pair is exactly that). External-
  hit counters include local CPU restores, so they cannot prove peer
  transfers on their own.
* Diff engine-side timing sums (`vllm:request_queue_time_seconds`,
  `vllm:request_prefill_time_seconds`, `vllm:time_to_first_token_seconds`)
  across each stage and reconcile them with client-observed TTFT. Client
  latency the engines never saw lives in the gateway path (router, render,
  sidecar), not the serving fleet.
* Run a low-rate independent probe through the gateway during stages. A
  latency plateau that is flat across offered rates and identical across
  arms is a fixed timeout somewhere in the path, not saturation - queueing
  grows with rate; timeouts do not.
