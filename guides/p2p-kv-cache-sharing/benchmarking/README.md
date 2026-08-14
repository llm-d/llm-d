# Benchmarking P2P KV cache sharing

The runnable workflow below uses the llm-d benchmarking framework
(inference-perf) against the gateway. Every scenario is preceded by the
guide's verification gates: a run where the mechanism is not provably
engaged measures nothing.

## Running the benchmark

A document-Q&A workload profile for `llmdbenchmark` ships via
[llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656),
which is still open, so until it merges the profile comes from the PR
fork at a pinned commit. The profile approximates the canonical
document-Q&A workload (128 requests in flight rotated across 192
sessions with 1-2 s tool delays); the canonical tables were measured
with a custom driver that admits 128 whole six-turn conversations at
once, archived with the measurement record. Expect the same regime, not
the same numbers.

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
cd llm-d-benchmark && source .venv/bin/activate
git fetch https://github.com/nilig/llm-d-benchmark.git \
    960f55a910fc4c049428b820b54462227dfda510
git checkout 960f55a910fc4c049428b820b54462227dfda510

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

Run the profile once per routing arm, switching only the EPP
configuration between runs. The three arm configs used for the gpt-oss
tables ship next to this file:

* [`epp-affinity.yaml`](epp-affinity.yaml) - precise prefix-cache
  routing.
* [`epp-load.yaml`](epp-load.yaml) - load-balanced placement, no pull
  (the recompute control).
* [`epp-load-p2p.yaml`](epp-load-p2p.yaml) - load-balanced placement +
  the pull (`minCachedTokenDelta: 2048`, from the crossover below).

The wide-EP testbed (`GLM-5.2-FP8`, 753B) ships three arm sets:

* `epp-glm-c64-{approx,precise,precise-p2p}.yaml` and
  `epp-glm-c64-approx-p2p-corrected.yaml` for the published C64
  complete-policy comparison and corrected four-arm observation. The archived
  `epp-glm-c64-approx-p2p.yaml` has a documented load-accounting selector
  mismatch and is retained only for provenance.
* `epp-glm-loadfirst{,-p2p}.yaml` for the synthetic load-spill A/B; placement
  is identical across the pair, so it isolates the source producer.
* `epp-glm-precise{,-p2p}.yaml` for the fixed 1P1D precise-affinity check. It
  isolates the source producer and records the mechanism-verified null where
  every eligible cached-token delta is zero.

For a defensible A/B, run arm pairs in both orders. When arm switches reuse
engine pods, the second arm inherits warm CPU tiers, so alternation measures
that sensitivity. The C64 protocol assigns new engine pod UIDs to every arm;
its reversed order checks time drift rather than inherited cache state.

Rig: `openai/gpt-oss-120b`, 16x TP=1 H200 (aggregated). Sizing inputs
measured on it: ~41.5 KB KV per token, ~1.22M tokens of GPU KV per pod
at `--gpu-memory-utilization=0.85`, CPU tier 88 GiB (~2.22M tokens,
~1.8x the GPU KV cache). Render service: 6 replicas (one replica
saturates near 10 req/s on ~50K-token prompts and flattens all arms to
the same false plateau - see the guide's Best Practices).

## What each scenario isolates

Two of the scenarios below change placement and the pull together, so their
headline margin is not a P2P margin. Read them for what they are:

| Scenario | The pull-isolating pair | Isolates the pull? |
| --- | --- | --- |
| Step 0 | recompute vs pull, same pod pair, no routing | **yes** |
| Wide-EP precise affinity (GLM) | `precise` vs `precise + pull` | **yes** - mechanism-verified null; no source delta reaches the threshold |
| Wide-EP load spill (GLM) | `load-first` vs `load-first + pull` | **yes** |
| Wide-EP C64 (GLM) | corrected `approximate` vs `approximate + pull`; `precise` vs `precise + pull` | **yes, for the single corrected observation** - the repeated headline remains a complete-policy comparison |
| Uniform pool | `load` vs `load + P2P` | **yes** |
| Hot set | `load` vs `load + P2P` | **yes** |
| Document Q&A | `affinity` vs `affinity + P2P` | partly - the winning arm (`load + P2P`) also changes placement |

The repeated C64 comparison and the document-Q&A headline compare complete
policies. The corrected C64 four-arm observation and the other scenarios carry
a control arm with identical placement and no pull, so their within-routing
margins isolate the configured pull factor. Comparisons *across* placement
policies (`affinity` vs `load + P2P`) answer a different question - which
deployment to run - and should not be read as P2P deltas.

The isolating pairs are where the feature's value is established: Step 0
(-56% to -88% TTFT with RDMA), the uniform pool (+143% sustained rate at 24
req/s), the hot set (+224% and 274 client-timeout failures eliminated at
48 req/s), and GLM load spill (-67% mean TTFT and 2.7x throughput). The
repeated C64 comparison answers which complete deployment carries more
successful work; its margin cannot be assigned to P2P alone. Its corrected
four-arm observation provides one mechanism-verified snapshot of the
individual factors, not a repeated effect estimate.

One recurring result, stated plainly: **under affinity placement the
pull is a fallback, not a throughput feature.** Affinity keeps KV local,
so `minCachedTokenDelta` is rarely met and there is little to fetch; the
measured `affinity + P2P` vs `affinity` delta sits inside run-to-run
spread. That is the pull behaving correctly as a recovery path. Choose
`affinity + P2P` for its placement behavior, not for throughput or
router-restart insurance (measured restart-recovery runs produced zero
pulls). See [When to use this path](../README.md#when-to-use-this-path).

## Measuring pull activity

No default-level signal reports both the count of successful pulls and
the tokens transferred. Use Prometheus metrics for measured arms; DEBUG
logs are for focused verification probes. The scenario writeups below
report session counts and offload-tier totals as pull evidence.

### Tier-labeled metrics

vLLM builds that include
[vllm#48798](https://github.com/vllm-project/vllm/pull/48798) label
offload counters by tier; the P2P tier's label value ends in `:p2p`,
which attributes activity to the peer tier directly:

```promql
sum(increase(vllm:kv_offload_tiering_read_bytes_total{tier=~".*:p2p"}[<stage>]))
sum(increase(vllm:kv_offload_tiering_block_hits_total{tier=~".*:p2p"}[<stage>]))
sum(increase(vllm:kv_offload_tiering_promotion_job_failures_total{tier=~".*:p2p"}[<stage>]))
```

`read_bytes_total` counts bytes read from the P2P tier into the CPU
tier - completed pull volume. `block_hits_total` counts blocks found in
the P2P tier at lookup; multiplied by `--block-size` it estimates
tokens found, not tokens transferred (a hit's promotion can still fail;
the failures counter reports those). The pinned `v0.27.1` engine image
does not contain vllm#48798; use the fallback below.

### Fallback: deltas against the no-pull control

`vllm:external_prefix_cache_hits_total` counts tokens the connector
reports as cached at scheduling time, before any load completes;
`vllm:kv_offload_load_bytes_total` counts bytes actually loaded into
GPU. Both include a pod restoring from its own CPU tier, so their
absolute values are offload-tier activity, not pull volume. The per-stage delta
against the matched no-pull arm (same placement, producer removed)
estimates pull-attributable activity; it is not an exact count, because
enabling the pull also changes later cache state. To attribute bytes to
a peer within a single run the consumer must hold no local copy, which
the
[calibration recipe](../../recipes/router/calibration/README.md#calibrating-mincachedtokendelta)
arranges with fresh token IDs and a no-pull control.

### Session establishment (default log level)

Source pods log `created connected session for <peer>` at INFO once a
peer's session is constructed; `accepting incoming connection` precedes
construction and can be followed by `rejecting peer`, so count the
former and check for `rejecting peer` and `peer down`. A nonzero count
is connectivity evidence, not pull evidence - one session serves many
pulls, reconnects add lines, and sessions persist for the engine
process lifetime, so a zero count in a later stage proves nothing and
only a transfer proves the mesh usable.

```bash
for pod in $(kubectl get pods -n "${NAMESPACE}" \
    -l llm-d.ai/guide=p2p-kv-cache-sharing -o name); do
  printf '%s: ' "${pod}"
  kubectl logs -n "${NAMESPACE}" "${pod#pod/}" -c modelserver \
    | grep -c 'created connected session'
done
```

Enumerate pods by name as above - with a label selector `kubectl logs`
defaults to the last 10 lines per pod, which silently undercounts.

### Per-pull accounting (requires `VLLM_LOGGING_LEVEL=DEBUG`)

`fetch RECEIVED kv_request_id=... round=... blocks=N` on the source
records a demanded fetch round; a closing lookup also emits a terminal
fetch with `blocks=0`, and one pull can span several rounds, so unique
`kv_request_id` values over rounds with `blocks > 0` count pull
attempts. The round's outcome is its
`finalize kv_request_id=... round=... success=...` line: success means
the round's full demand was sent, so `blocks=N` summed over rounds
that finalize `success=True`, times `--block-size`, gives tokens
moved. DEBUG emits per-block and per-round lines - use it for probes,
not measured arms.

## Step 0 - pull-versus-recompute crossover (single request)

Seed a fresh prefix on one pod; measure single-request prefill latency
on a cold pod with and without the pull, at prefix lengths
2K/8K/16K/32K/48K. The crossover sets the router's
`minCachedTokenDelta`: below it a pull costs more than recomputing. The
measurement is automated as a
[calibration recipe](../../recipes/router/calibration/README.md#calibrating-mincachedtokendelta).
Calibrate on a *warmed* pod pair: the first pull between two peers pays
a one-time session-establishment cost (~6 s measured on the wide-EP
testbed) that steady-state pulls never see.

The ladder is transport-dependent: it was measured with `rdma/ib` on
the pods. The published benchmark does not include a TCP comparison, so
calibrate separately on TCP (see
[Supported Hardware Backends](../README.md#supported-hardware-backends)).
`ls /dev/infiniband` in the container shows whether RDMA is available.

The measured ladder is canonical in
[the gpt-oss results page](../benchmark-results/gpt-oss-120b-h200.md#pull-versus-recompute-single-request):
the pull wins at every measured length, -55.8% prefill latency at 2K
widening to -88.2% at 48K. gpt-oss's compact hybrid-attention KV (41.5 KB/token)
makes the transfer cheap enough to beat even its fast MoE prefill
(~29K tokens/s on H200), and the pull additionally removes the prefill
work from the fleet - which the pool scenarios measure.
`minCachedTokenDelta: 2048` is the smallest measured winning length.

## Uniform shared-prefix pool (three routing arms)

128 shared prefixes x 48K tokens (~6M-token working set, ~5x one pod's
GPU cache), 256-token questions, 64 output tokens, streaming,
constant-rate stages ramped past saturation. `load.request_timeout` set
explicitly.

Arms (identical workload and pods; only the router config changes):

1. `epp-affinity.yaml` - uniform pools are affinity's best case; the
   reference ceiling.
2. `epp-load.yaml` - every cross-pod request recomputes its prefix; the
   recompute floor.
3. `epp-load-p2p.yaml` - load-balanced placement + pull.

Metrics per arm: achieved vs offered rate, TTFT and request latency
p50/p95, established P2P session counts, external-hit deltas, per-pod
served counts, restarts (must be 0).

Measured (achieved req/s / TTFT p50 / request latency p50 per stage):

| offered | affinity | load, no P2P | load + P2P |
| --- | --- | --- | --- |
| 6 req/s | 5.97 / 207 ms / 0.50 s | 5.59 / 2.5 s / 5.6 s | 5.96 / 342 ms / 0.64 s |
| 12 req/s | 11.92 / 200 ms / 0.49 s | 9.02 / 8.6 s / 26.2 s | 11.49 / 460 ms / 0.98 s |
| 18 req/s | 17.87 / 192 ms / 0.48 s | 8.58 / 26.0 s / 45.7 s | 17.46 / 341 ms / 0.67 s |
| 24 req/s | 23.82 / 191 ms / 0.48 s | 9.01 / 43.8 s / 63.4 s | 21.93 / 344 ms / 0.70 s |
| 30 req/s | 29.76 / 184 ms / 0.48 s | 9.21 / 61.3 s / 81.2 s | 29.19 / 342 ms / 0.73 s |

Zero failures and zero restarts in all arms (16,200 requests). Pull
evidence in the `load + P2P` arm: **120 established P2P sessions**,
against 0 in the arms without the producer.

The tier also served 210M external-hit tokens and 7.8 TB (GPU hit rate
17.3%) - offload-tier activity, not pull volume; see
[Measuring pull activity](#measuring-pull-activity).

Reading the arms: affinity is near-ideal here - each pod owns ~8 of the
128 prefixes (384K tokens, comfortably GPU-resident), so every request
is a local hit. The recompute control saturates near 9 req/s: every
cross-pod placement re-prefills 48K tokens. **The pull sets load
placement's floor**: `load + P2P` tracks offered rate through 30 req/s
at sub-second p50 - at rate 24 that is 9.01 -> 21.93 req/s (+143%) and
63.4 s -> 0.70 s p50 against the recompute floor. Affinity remains the
better arm on this workload (0.48 s vs 0.73 s p50) because scattering
pays transfer work affinity never pays - but the gap is a constant
factor, not a collapse. The document-Q&A headline is where load-aware +
P2P's spreading matters more; see the
[placement rule](../README.md#when-to-use-this-path).

## Hot set larger than one pod's cache

A small hot set takes all traffic, decode-heavy requests (512 output
tokens), rates ramped past what the prefix owners alone can absorb.
Affinity concentrates each hot prefix's work on its owner pod;
load-aware placement plus the pull serves the same hot content from the
whole fleet.

**Size the hot set against one pod's GPU KV capacity before running
this - that ratio decides the result.** Measured on 16x gpt-oss-120b
(~1.22M tokens of GPU KV per pod), walking 48K-token prefixes:

| hot set | vs one pod's cache | what happens |
| --- | --- | --- |
| 8 prefixes (384K tok) | 0.31x | fits in every pod; every arm serves GPU hits after warmup, nothing pulls |
| 32 prefixes (1.54M tok) | 1.26x | one stage of churn, then replication absorbs it and the arms converge |
| **64 prefixes (3.07M tok)** | **2.5x** | **misses are permanent; the regime this scenario is about** |

Measured at 64 x 48K (achieved req/s / TTFT p50 / request latency p50):

| offered | `affinity` | `load` - no P2P | `load + P2P` |
| --- | --- | --- | --- |
| 12 req/s | 11.94 / 188 ms / 0.30 s | 9.31 / 7.9 s / 16.6 s | 11.84 / 310 ms / 0.42 s |
| 24 req/s | 23.04 / 183 ms / 0.31 s | 11.47 / 24.3 s / 34.5 s | 22.83 / 271 ms / 0.42 s |
| 36 req/s | 34.03 / 190 ms / 0.36 s | 11.77 / 47.0 s / 61.6 s | 34.34 / 249 ms / 0.45 s |
| 48 req/s | 46.03 / 196 ms / 0.38 s | 13.85 / 58.2 s / 72.5 s, **274 failures** | 44.93 / 254 ms / 0.48 s, **0 failures** |

Pull evidence: 120 P2P sessions, 204M external-hit tokens, 7.5 TB served
from the offload tier, GPU hit rate 43.2% (the set genuinely does not
fit).

**The pull is the difference between a serving fleet and a shedding
one.** Same placement, pull as the only variable, at offered 48:
13.85 -> 44.93 req/s (+224%), TTFT p50 58.2 s -> 254 ms, 274
client-timeout failures -> zero. The pull arm tracks offered rate to 48
within 2% of affinity's throughput.

Affinity does not suffer here: 64 prefixes over 16 pods spread ~4 per
pod, no owner is overloaded, and affinity holds 46 req/s at 196 ms.
Owner concentration is a separate pathology that needs a prefix count
well below the pod count - at which point the set also fits everywhere.
Choose which pathology you are testing and size accordingly.

## Document Q&A at session scale (the headline)

The user-facing regime: 192 conversations, each with a private
48K-token document prefix, 6 short questions each (256-token answers),
128 conversations concurrent. Per-turn decode is small, so TTFT
dominates. With ~9.2M tokens of document prefix across the fleet and a
fixed per-document ownership rule, placement - not aggregate GPU
capacity - decides whether a question is a cache hit, a 48K recompute,
or a wait behind someone else's document.
`guide_p2p-kv-cache-sharing_1.yaml` approximates this regime; scale
`num_conversations` and `concurrency` to your fleet's pod count so
enough sessions contend for a limited set of owner pods.

Results are canonical in
[the gpt-oss results page](../benchmark-results/gpt-oss-120b-h200.md#document-qa-the-headline),
measured at the shipped `podCacheSize: 32` with a per-arm cold roll.
Headline: load-aware + P2P wins - 1.5x better p99 TTFT and +35%
throughput than precise routing warm; cold, zero failures at 2.9x
throughput and 7.9x p99, while the affinity arms take 47-48 client
timeouts as placement collapses onto one pod. Precise + P2P versus
precise alone reads +17% throughput warm - inside run-to-run spread, so
not credited to the pull.

On this scenario alone `epp-load-p2p` is the better arm; on
[the uniform pool](#uniform-shared-prefix-pool-three-routing-arms) the
result reverses. The guide ships `epp-affinity-p2p` as the safer
general-purpose default; reach for `epp-load-p2p` when your workload
looks like this one.

## Wide-EP testbed (GLM-5.2-FP8)

The GLM tests use two distinct 32x H200 topologies. The crossover and
synthetic load-spill tests use one 16-way prefill instance and one 16-way
decode instance. The C64 policy comparison uses two prefill pods and two
decode pods, each with DP 8 and TP 1.

The published C64 campaign used these exact configurations:

* [`epp-glm-c64-approx.yaml`](epp-glm-c64-approx.yaml) - calibrated
  approximate routing without P2P.
* [`epp-glm-c64-approx-p2p.yaml`](epp-glm-c64-approx-p2p.yaml) - the archived
  approximate+P2P arm. Its renamed prefix producer is not selected by the
  parameterless in-flight load producer, so the published observation is
  confounded and retained only for provenance.
* [`epp-glm-c64-approx-p2p-corrected.yaml`](epp-glm-c64-approx-p2p-corrected.yaml) -
  the corrected approximate+P2P arm used by the August 14 observation.
* [`epp-glm-c64-precise.yaml`](epp-glm-c64-precise.yaml) - DP-aware precise
  KV events without the source producer.
* [`epp-glm-c64-precise-p2p.yaml`](epp-glm-c64-precise-p2p.yaml) - the precise
  policy with the source producer.

The precise arms use the token producer's `vllm` backend. Their URL records the
benchmark testbed's `glm-5-2-render` Service, which selects the prefill vLLM
APIs directly; the testbed has no separate renderer Deployment. This guide's
equivalent podless Service is `p2p-kv-cache-sharing-render`: Service port 8000
targets the model-server vLLM port 8200 because pod port 8000 belongs to the
routing-proxy sidecar and does not serve `/render`. Set `modelName` and
`vllm.url` for that deployment when adapting a benchmark arm.

Each arm starts after all four engine pods receive new UIDs. Run AIPerf with
the same public trace, seed, admission window, and drain:

```bash
aiperf profile \
  --url "$EPP_URL" \
  --model zai-org/GLM-5.2-FP8 \
  --endpoint-type chat \
  --streaming \
  --public-dataset semianalysis_cc_traces_weka_062126 \
  --no-fixed-schedule \
  --concurrency 64 \
  --num-dataset-entries 48 \
  --synthesis-max-isl 115000 \
  --synthesis-max-osl 2048 \
  --benchmark-duration 300 \
  --benchmark-grace-period 120 \
  --random-seed 67 \
  --ui None \
  --output-artifact-dir "$ARTIFACT_DIR"
```

Count requests only if they reach a terminal state within the 300-second
admission window. Across three comparisons - two approximate-first and one
precise+P2P-first - precise+P2P improves successful throughput over
approximate without P2P by 5.18% to 13.77%, with a 9.97% paired median. The
p90 end-to-end latency improves in two windows and regresses by 1.17% in the
reversed-order window, so the repeatable result is capacity rather than a
latency guarantee. Latency percentiles include only successful terminal
requests inside the cutoff and therefore compare different-sized,
right-censored populations.

The corrected 300-second window includes all four intended combinations. Relative
to approximate routing without P2P, approximate+P2P is +4.61% in successful
throughput, precise without P2P is +1.27%, and precise+P2P is +11.07%.
Approximate+P2P records 37 peer-load submissions, 65 successful rounds, zero
failed rounds, and 23,821 submitted blocks. Precise+P2P records 18
submissions, 14 successful rounds, zero failed rounds, and 12,079 blocks. Both
no-P2P arms record zero peer activity. This is one fixed-window observation,
so it shows an interaction-shaped result but not repeated single-factor
effects. The archived confounded observation remains in the data bundle for
provenance.

The C64 configurations use `minCachedTokenDelta: 2048`, below the separate
upstream-tier crossover recommendation of 12,288 tokens. Use the crossover
recipe to calibrate production deployments. Full tables, mechanism evidence,
crossover sweep, and the quarantined overlay-era grid are in
[the GLM results page](../benchmark-results/glm-5.2-h200.md).

## Run hygiene

* Compare each stage's wall-clock to send-window + drain; a stretched
  wall with fast successes means hung requests, not slow serving.
* `report.request_lifecycle.per_request: true` - per-request records
  make hangs and tails attributable.
* Record pull evidence per arm. For a scenario preregistered to pull,
  zero engagement means a misconfigured run; for an affinity arm a
  mechanism-verified zero is a legitimate null. External-hit counters
  include local CPU restores and cannot prove peer transfers alone.
* Diff engine-side timing sums (`vllm:request_queue_time_seconds`,
  `vllm:request_prefill_time_seconds`,
  `vllm:time_to_first_token_seconds`) per stage and reconcile with
  client-observed TTFT; latency the engines never saw lives in the
  gateway path.
* Fleet stability gates arm validity: record engine pod ages before and
  after every arm and discard any arm whose fleet changed mid-flight. A
  keep-warm must ping every engine pod directly, not just the gateway.
* Size the decode pool for live fan-out so `branch width x context tokens`
  does not exceed available decode KV. Treat zero or missing TTFT alongside
  engine exits as an invalid arm even if the benchmark export completes.
* Restarting the EPP empties the precise index; declare a cold-or-warm
  protocol and apply it to both arms. Re-verify per-rank KV-event
  subscriptions after every EPP restart with a live socket check - they
  establish seconds after Ready, so poll rather than sample once.
* Verify the instrument can detect absence before trusting a zero:
  `kubectl logs --tail=N` may contain no startup lines at all; assert
  the capture holds lines that must exist before believing "plugin
  absent".
* Run a low-rate independent probe through the gateway during stages. A
  latency plateau flat across offered rates and identical across arms
  is a fixed timeout in the path, not saturation - queueing grows with
  rate; timeouts do not.
