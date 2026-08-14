# zai-org/GLM-5.2-FP8 P2P KV Cache Sharing Benchmark on vLLM (wide-EP, H200)

This page combines two 32x H200 `zai-org/GLM-5.2-FP8` (753B MoE)
prefill/decode-disaggregated cells. The pull-versus-recompute, load-spill, and
precise-affinity campaigns use one 16-way prefill instance and one 16-way
decode instance with `minCachedTokenDelta: 16384`. The C64 policy comparison
uses two 8-way prefill instances and two 8-way decode instances with
`minCachedTokenDelta: 2048`. Both cells use vLLM block size 64, a 100 GiB CPU
offload tier per rank, and NIXL transfers. The page also quarantines the
overlay-era four-arm grid on recorded SemiAnalysis Weka agentic traces. The
precise and load-first configurations ship as the `epp-glm-*.yaml` files in
[../benchmarking/](../benchmarking/README.md); the C64 comparison ships as the
`epp-glm-c64-*.yaml` files.

## Pull versus recompute (single request)

Consumer = the prefill leader (the P/D-relevant direction), source = the
decode leader, direct `kv_transfer_params` injection with no EPP or
sidecar in the path, fresh random token IDs per probe, warmed transfer
mesh, medians of 3 with the first pull discarded. Every pull verified
byte-exact against the consumer's `vllm:kv_offload_load_bytes_total`
(92.6 KB/token, constant across every length):

| prefix tokens | recompute | P2P pull | delta | pulled in |
| ---: | ---: | ---: | ---: | ---: |
| 4,096 | 672.2 ms | 1,262.2 ms | +87.8% | 379.4 MB |
| 8,192 | 1,067.8 ms | 1,170.6 ms | +9.6% | 758.8 MB |
| 12,288 | 1,708.5 ms | 1,241.0 ms | **-27.4%** | 1,138.2 MB |
| 16,384 | 2,148.3 ms | 1,268.9 ms | -40.9% | 1,517.6 MB |
| 24,576 | 3,338.0 ms | 1,315.3 ms | -60.6% | 2,276.4 MB |

Recompute is linear at ~130-147 us/token while the pull is flat at
about 1.25 s, so the **crossover is ~8,650 tokens**. The recommended
setting is `minCachedTokenDelta: 12288`: 8,192 is a dead tie whose sign
flips between runs, and 12,288 is the lowest length the sweeps call
decisively. The 1P1D load-spill and precise-affinity campaigns ran `16384` -
set from the earlier sweep quarantined below - which sits above either
measured tie. The 2P2D C64 campaign instead ran `2048`, as scoped in its
section.

Two measurement controls worth repeating on any rig: at 12,288 the
identical seeded probe *without* the injected source-pull
`kv_transfer_params` block never pulls (0.0 MB loaded against 1,138.2 MB
with it, three reps) - the engine does not fetch from peers on its own;
the router/sidecar directive is the trigger, and the loaded-bytes counter
is peer-attributable here because the consumer has never seen the token
IDs. And the *first* pull between a fresh pod pair pays a one-time ~6 s
session-establishment cost that steady-state pulls never see - calibrate
on a warmed pair or the transient reads as the pull's cost.

### Historical: the overlay-era sweep (superseded)

The same method on the overlay-era stack measured a higher pull floor
(~1.7-2.3 s: session floor plus ~4.5 GB/s effective transfer) and a
correspondingly later tie at 13,648 tokens; the campaigns' `16384`
setting dates from this sweep. Retained for provenance only - the
upstream-tier table above is the current calibration:

| prefix tokens | recompute | P2P pull | delta |
| --- | --- | --- | --- |
| 8,070 | 1.00 s | 1.69 s | +69% |
| 13,648 | 1.74 s | 1.76 s | tie |
| 21,617 | 2.76 s | 1.80 s | -35% |
| 34,214 | 4.51 s | 2.51 s | -44% |
| 48,109 | 6.38 s | 1.98 s | -69% |
| 65,111 | 8.78 s | 1.98 s | -78% |
| 98,220 | 13.75 s | 2.29 s | -83% |

## Load spill and the pull's payoff (matched c32 benchmark)

The system-level payoff measurement: a load-first prefill policy
(`precise-prefix-cache-scorer` weight 1 + `queue-scorer` weight 3 +
`active-request-scorer` weight 1) with and without `p2p-source-producer`
(`minCachedTokenDelta: 16384`) as the only difference. Under this policy
the picker spills requests off the cache holder whenever queues build, so
without the pull a spilled ~70K-token prompt recomputes its prefix; with
it, the prefix follows the request. Per repetition: a fresh salted
~70K-token prefix, 3 warmups, 96 measured requests at concurrency 32.
Three repetitions per mode in counterbalanced order, the EPP restarted
and probed on every profile swap. The two profiles are
`epp-glm-loadfirst.yaml` and `epp-glm-loadfirst-p2p.yaml` in
[../benchmarking/](../benchmarking/README.md).

| mode | TTFT mean (s) | TTFT p90 (s) | req/s | wall per rep (s) |
| --- | ---: | ---: | ---: | ---: |
| precise, no pull | 7.85 | 21.3 | 3.80 | 25.4 |
| precise + P2P | **2.56** | **5.00** | **10.10** | 9.5 |
| change | **-67%** | **-77%** | **2.7x** | -63% |

All 576 requests across both modes returned 200; per-repetition spread is
tight (precise 7.53-8.44 s mean, pull 2.45-2.64 s). The result has been
measured twice independently - once on the original fix build (-70% mean
TTFT, 2.80x) and once on separately built images of the same code with a
freshly booted fleet and fresh salts (-67%, 2.66x); every repetition of
the second run lands in or adjacent to the first run's per-repetition
bands.

The mechanism is visible in the tail: the no-pull mode's ~21 s p90 is the
spill tail (recompute of a 70K-token prefix on a non-holder), and the
pull collapses it to ~5 s - a flat transfer cost (the crossover floor
plus concurrency-32 queueing) paid instead of the linear recompute.

The boundary on the other side: under holder-affinity policies (affinity
weight 5) with a correctly sized index, the pull rarely fires on
recurring-prefix traffic and arms tie - placement already lands requests
on the cache, and live sampling shows every source evaluation at a
cached-token delta of zero. **The pull converts load-spill recompute
into a flat-cost transfer; where routing trades affinity for load
balance, it recovers the cache reuse that placement gives up.** It is a
property of the policy-workload pair, not a general model speedup.

Attribution note: the six repetitions above are a producer-only A/B (the
two profiles differ by `p2p-source-producer` alone) and did not record
per-repetition transfer counters; pull-path liveness on this build is
established separately by the correlated single-request proof (per-rank
attribution, source accept on the rank-offset port, consumer load equal
to tokens x ~93 KB, HTTP 200).

## 2P2D agentic C64 policy comparison

The [GLM-5.2 agentic-serving
study](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d)
describes the production coding-agent trace shape and wide-EP serving
architecture. This benchmark tests how the routing policy behaves when exact
cache-location information is paired with peer retrieval under saturation.

The cell has two 8-way data/expert-parallel prefill pods and two 8-way decode
pods (32x H200), GLM-5.2-FP8, vLLM block size 64, and a 100 GiB CPU offload tier
per prefill rank. AIPerf replays 48 entries from the SemiAnalysis Weka
coding-agent trace corpus with seed 67 and no fixed root schedule at
concurrency 64.

Each arm starts with new UIDs for all four engine pods. Requests count only if
they reach a terminal state by `min(credit_issued_ns) + 300 seconds`;
completions during the 120-second drain do not enter the result. The fixed
window measures successful capacity under saturation, not the time required
to complete the full workload.

The per-run exact-window summaries and the aggregate used for the tables are
committed in the [C64 data bundle](./data/glm-5.2-c64/README.md). The bundle
records the campaign roles, accounting rule, raw metric precision, and
formulas used to derive the published changes.

### Repeated complete-policy comparison

The repeated baseline is calibrated approximate routing without P2P. The
candidate combines DP-aware precise KV events, speculative indexing, GPU/CPU
cache weights 1.0/0.4, and `p2p-source-producer` with
`minCachedTokenDelta: 2048`. Both use `peakPrefillThroughput: 5541` and
`maxTTFTPenaltyMs: 55000`. The comparison belongs to the complete routing
policy, not to precision or P2P alone.

| observation | arm order | approximate successful req/s | precise+P2P successful req/s | successful req/s change | input Ktok/s change | E2E p90 change |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | approximate then precise+P2P | 3.077 | 3.383 | +9.97% | +12.86% | -13.81% |
| 2 | approximate then precise+P2P | 2.977 | 3.387 | +13.77% | +17.19% | -12.21% |
| 3 | precise+P2P then approximate | 3.217 | 3.383 | +5.18% | +5.26% | +1.17% |
| paired mean | - | 3.090 | 3.384 | +9.64% | +11.77% | -8.28% |
| paired median | - | 3.077 | 3.383 | +9.97% | +12.86% | -12.21% |

The summary arm columns are the means or medians of the three arm values. The
change columns are the means or medians of the three pairwise percentage
changes, so a change cell is not expected to equal the ratio of the two
summary arm cells.

Successful throughput improves in all three observations. The latency signal
is less stable: two observations improve p90 end-to-end latency while the
reversed-order observation is 1.17% worse. The supported result is a repeated
capacity improvement, not a guaranteed latency reduction in every fixed
window. Latency percentiles include only successful requests that reached a
terminal state before the cutoff, so they compare different-sized,
right-censored populations and are directional evidence only.

Precise+P2P successful throughput is 3.383, 3.387, and 3.383 requests/s, with
a population coefficient of variation below 0.1%. Approximate throughput ranges
from 2.977 to 3.217 requests/s, a 3.19% coefficient of variation. Three
observations are not enough to characterize a distribution, but the candidate
is more stable in this sample.

### Corrected four-arm observation

One August 14 300-second fixed-window observation includes all four intended
policy combinations under the same cutoff. The approximate+P2P arm binds
`inflight-load-producer`, `prefix-cache-affinity-filter`, and
`p2p-source-producer` to the same named approximate producer:

| routing | P2P | successful req/s | input Ktok/s | TTFT p50/p90 (s) | E2E p50/p90 (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| approximate | no | 2.890 | 142.065 | 2.691 / 20.030 | 10.961 / 35.438 |
| approximate | yes | 3.023 | 149.041 | 2.184 / 19.623 | 9.697 / 31.705 |
| precise | no | 2.927 | 143.618 | 2.899 / 20.474 | 11.474 / 31.942 |
| precise | yes | 3.210 | 163.805 | 2.018 / 16.397 | 8.772 / 29.497 |

Relative to approximate routing without P2P, approximate+P2P is +4.61% in
successful throughput, precise without P2P is +1.27%, and precise+P2P is
+11.07%. Median TTFT changes by -18.85%, +7.71%, and -25.02%, respectively;
median end-to-end latency changes by -11.53%, +4.69%, and -19.97%. This is one
fixed-window observation, so it shows an interaction-shaped result but does
not provide repeated estimates of either single-factor effect.

The August 13 four-arm source remains in
`four-arm-exact-window.json` for provenance. Its approximate+P2P arm is
confounded because `inflight-load-producer` reads a different prefix producer;
it is not used in this corrected table.

### Mechanism evidence

Across the three repeated comparisons, approximate prefill queue p90 is 12.8,
13.7, and 13.5 requests; precise+P2P is 9.0, 8.0, and 8.0. Approximate
external prefill hit rate is 1.66%, 2.64%, and 2.36%; precise+P2P is 38.47%,
12.08%, and 39.93%. NIXL records zero failed transfers, failed notifications,
and expired requests. External-hit rate establishes engagement, not the size
of the capacity gain; queue p90 is the consistent signal across the windows.

The mechanism logs for the archived August 13 fixed-window observation record
13 source request IDs, 12 peer-load submissions, 12 unique transfer IDs, 17
successful transfer rounds, zero failed rounds, and 6,342 submitted KV blocks.
These counters instrument
different pipeline stages and are not expected to be one-to-one: a source
directive need not submit a peer load, and one transfer can complete in
multiple rounds. No byte volume is derived from the block count because the
C64 offload quantum does not match the separate 1P1D crossover's 92.6 KB/token
measurement.
`vllm:nixl_bytes_transferred` also counts normal prefill-to-decode traffic,
and `vllm:kv_offload_load_bytes` aggregates local and peer tier loads.

The corrected observation verifies zero P2P source directives, peer-load
submissions, successful rounds, and transferred blocks in both no-P2P arms.
Approximate+P2P records 46 source request IDs, 37 peer-load submissions, 37
unique transfer IDs, 65 successful rounds, zero failed rounds, and 23,821
submitted blocks. Precise+P2P records 19 source request IDs, 18 submissions,
18 unique transfer IDs, 14 successful rounds, zero failed rounds, and 12,079
submitted blocks. At the run's 3,502,592-byte submitted-block payload, the
block counts correspond to 83,435,244,032 bytes (77.705 GiB) and
42,307,808,768 bytes (39.402 GiB), respectively. These values derive from the
P2P submission records, not the generic offload or NIXL aggregate counters.

The DP-aware event path does not collapse traffic onto rank 0. Across the three
precise+P2P observations, the two rank-0 engines account for 8.3% to 17.1% of
prefill successes; 12.5% is the even two-rank share across 16 ranks. The
largest individual rank accounts for 11.4% to 12.3%, versus a 6.25% even
per-rank share, so the observation rules out rank-0 collapse but not rank
imbalance.

### Configuration boundary

The run uses `minCachedTokenDelta: 2048`, below this page's separate GLM
crossover recommendation of 12,288 tokens. It describes the policy as
configured and does not establish that 2,048 is the best production setting.
The corrected four-arm observation removes the load-accounting selector
confound, but both middle arms still require repeated, counterbalanced
observations before assigning the gain quantitatively to precision or P2P.

## Historical: the overlay-era four-arm ladder (superseded)

The ladder below was measured on the overlay-era stack with the
producer's `podCacheSize` left at its default. That default evicts legitimate
(endpoint, tier) holders on a 32-rank fleet, so the scheduled pod and
the best peer read different cached-token counts for the same
physically-replicated prefix - the pull then "rescued" a divergence the
index itself manufactured. On the fixed stack with `podCacheSize: 64`
the same precise-affinity pair is a mechanism-verified null (zero
source-delta evaluations), so the precise-affinity improvements and the
approximate-index pull volumes below do not reproduce and should not be
cited as feature results. The tables are retained as a reproduction
record of the index-sizing failure mode.

TTFT p50 / p90 (ms) per cell; the pull's delta against the same placement
without it in parentheses:

| conc | approx | approx + P2P | precise | precise + P2P |
| --- | --- | --- | --- | --- |
| 32 | 1,665 / 4,095 | 1,621 / 3,917 | 2,265 / 7,557 | 1,649 (-27%) / 4,136 (-45%) |
| 64 | 2,234 / 4,897 | 2,276 / 5,449 | 2,801 / 9,823 | 2,581 (-8%) / 7,139 (-27%) |
| 128 | 2,963 / 9,226 | 2,953 / 8,833 | 3,802 / 11,755 | 3,177 (-16%) / 9,970 (-15%) |

Invalidated historical interpretation (do not quote without the
quarantine above):

* **The pull is precise affinity's safety net.** On agentic traces affinity
  concentrates sessions on the ranks that hold their cache; the pull lets
  the picker place on a less-loaded rank and fetch the prefix there. At
  concurrency 32 it erases the concentration penalty entirely - precise +
  P2P (1,649 / 4,136) ties the best load-balanced cell in the grid. Pull
  volume under precise: 41 / 93 / 163 GB at c32/c64/c128.
* **The pull fires from the approximate index too.** The approx + P2P arm
  drove 33.8 GB of pulls at c128 from the prompt-hash estimate alone - the
  `p2p-source-producer` consumes either index, so the pull does not require
  the KV-event pipeline. The approximate arms' fuzzier estimates spread
  placement more, so there is less concentration for the pull to rescue -
  consistent with the aggregated testbeds' composition rule: the pull pays
  where placement diverges from cache.
* **Arm parity notes.** TTFT p99 is within single-run noise across arms at
  every concurrency (the worst case everywhere is the cold first prefill of
  a long context). The smaller precise+P2P p50 deltas at c64/c128 (-8%,
  -16%) sit closer to single-run noise than the c32 result (-27%) too -
  treat the concentration-penalty finding as strongest at c32 until
  repeated runs confirm the higher-concurrency deltas. The approx + P2P
  arm ran the engine with
  `offload_prompt_only: true` - the matched setting for a placement whose
  index never covers decode blocks, and for models whose reasoning decode
  is not reused as a next-turn prefix (GLM re-renders without it).

At every concurrency the approximate arms lead or tie the precise arms on
this workload - the exact index concentrates the corpus's contending
sessions onto their cache holders and pays in queues, while the fuzzier
estimates spread them - the same placement-under-contention regime the
aggregated document Q&A testbed measured. The value the pull adds here is
making the precise affinity policy competitive again where it is deployed
as the default.
