# zai-org/GLM-5.2-FP8 P2P KV Cache Sharing Benchmark on vLLM (wide-EP, H200)

The benchmark runs `zai-org/GLM-5.2-FP8` (753B MoE) prefill/decode
disaggregated, one prefill and one decode instance, each 16-way
data/expert-parallel across 2 pods (32x H200 total), ~520K tokens of GPU KV
and a 100 GiB CPU offload tier per rank, vLLM block size 64, KV transfers
over NIXL. Routing uses the llm-d inference gateway with the precise
(KV-event-fed) prefix index, `minCachedTokenDelta: 16384` (set from the
overlay-era crossover; the current upstream-tier crossover recommends
`12288` - see below). The page carries the pull-versus-recompute crossover,
the load-spill payoff pair, and - quarantined at the end - the
overlay-era four-arm grid on recorded agentic traces (the SemiAnalysis
Weka corpus, aiperf at concurrencies 32/64/128). The precise and load-first arm configurations ship as the
`epp-glm-*.yaml` files in [../benchmarking/](../benchmarking/README.md);
the historical grid's approximate-index arms are not shipped.

## Pull versus recompute (single request)

Consumer = the prefill leader (the P/D-relevant direction), source = the
decode leader, direct `kv_transfer_params` injection with no EPP or
sidecar in the path, fresh random token IDs per probe, warmed transfer
mesh, medians of 3 with the first pull discarded. Every pull verified
byte-exact against the consumer's `vllm:kv_offload_load_bytes_total`
(92.6 KB/token, constant across every length):

| prefix tokens | recompute | P2P pull | delta | pulled in |
|---:|---:|---:|---:|---:|
| 4,096 | 672.2 ms | 1,262.2 ms | +87.8% | 379.4 MB |
| 8,192 | 1,067.8 ms | 1,170.6 ms | +9.6% | 758.8 MB |
| 12,288 | 1,708.5 ms | 1,241.0 ms | **-27.4%** | 1,138.2 MB |
| 16,384 | 2,148.3 ms | 1,268.9 ms | -40.9% | 1,517.6 MB |
| 24,576 | 3,338.0 ms | 1,315.3 ms | -60.6% | 2,276.4 MB |

Recompute is linear at ~130-147 us/token while the pull is flat at
about 1.25 s, so the **crossover is ~8,650 tokens**. The recommended
setting is `minCachedTokenDelta: 12288`: 8,192 is a dead tie whose sign
flips between runs, and 12,288 is the lowest length the sweeps call
decisively. The benchmark campaigns on this page ran `16384` - set from
the earlier sweep quarantined below - which sits above either measured
tie, so their fired pulls are always in the win region.

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
|---|---|---|---|
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
|---|---:|---:|---:|---:|
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

## Recorded fork replay: the spill pair on a real agentic trace

The load-spill result reproduced on a recorded workload instead of salted
prefixes: Claude Code sessions from `semianalysisai/cc-traces-weka-062126`
that fork tens of subagents over one exact shared prefix, replayed with
AIPerf (`weka_trace`, fixed schedule, `ignore_eos`). Cell: 2 prefill + 2
decode DP8 instances (32x H200), fp8 KV (~54.7 KB/token on this build),
`epp-glm-tokenload{,-p2p}.yaml` as the only per-arm difference,
`minCachedTokenDelta: 12288`. Protocol and preconditions:
[../benchmarking/README.md](../benchmarking/README.md) (fork-replay
section); raw artifacts, pre-registered windows, and tooling live on the
`p2p-findings` branch (`test/p2p-findings/configs/agentx-fork-sweep/`).

**Per-pull value, verified at three layers.** On the W=8 group (75,520-token
prefix), one spilled branch start pulled 77,312 tokens and returned in
**790 ms** against the 13.8-13.9 s cold price both arms pay at the seed -
within 6% of the value pre-registered from earlier runs' recompute rate
(implied 5,861 tok/s avoided prefill vs 6,069/6,273 measured on a second
cell). Evidence chain: the router directive (`delta=77,376 -> set KV cache
source header`), the source engine's transfer session (decode DP3,
`accepting incoming connection ... created connected session`), and the
destination counter (`external_prefix_cache_hits += 77,312`, frozen through
the zero-pull control).

**Fork-level effect: the straggler band is removed.** Matched twin windows
(43 and 44 children, ~40K prefixes within 0.8%) run as each other's control
with cold engines between pairs, then swapped:

| comparison | without P2P | with P2P |
|---|---:|---:|
| same window (W=43), both cold | p90 11.2 s (6 stragglers, 11-12.5 s) | **p90 1.6 s** |
| matched twins, forward | p90 4.7 s | p90 1.6 s |
| reversed twins | p90 11.2 s | p90 7.2 s (wider burst head) |
| median, every arm | ~1.2-1.3 s | ~1.2-1.3 s |

Simultaneous cold recomputes compound: 6.6 s alone becomes 11-12.5 s when
six run at once. The pull's residual tail is the burst head - siblings
arriving before any copy of the prefix exists, which no mechanism serves.
The median never moves; on a fork the pull is tail repair, consistent with
the load-first result's shape and with independent runs on a second cell
(aggregate flat, p99 -43/-37%).

**Negative result, single prefill pod.** With one prefill instance, pulls
fired (5, engine-confirmed, 352,000 tokens) but lost to a faster same-pod
path (~1-1.8 s baseline vs 3.5-4.2 s pulled); the fast path is not yet
attributed. Router-level P2P pays across pod boundaries; within one pod the
engine stack already rescues spills more cheaply.

**Decode sizing bound.** 44 children x 40K tokens (~1.76M live decode KV)
against a single DP8 decode instance (~1.68M) crashed the engine mid-run,
and the dead-fleet run still emitted a complete-looking export with zero
TTFTs. Size decode so `width x context <= decode KV`; with flow control
(`utilization-detector` + `flowControl`) enabled, the identical
single-decode configuration completed 163/164 with zero engine errors
(causal isolation of the flow-control contribution pending a deliberate
control).

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
|---|---|---|---|---|
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
