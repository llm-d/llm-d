# zai-org/GLM-5.2-FP8 P2P KV Cache Sharing Benchmark on vLLM (wide-EP, H200)

The benchmark runs `zai-org/GLM-5.2-FP8` (753B MoE) prefill/decode
disaggregated, one prefill and one decode instance, each 16-way
data/expert-parallel across 2 pods (32x H200 total), ~520K tokens of GPU KV
and a 100 GiB CPU offload tier per rank, vLLM block size 64, KV transfers
over NIXL. Routing uses the llm-d inference gateway; the four arms cross the
prefix-affinity index (precise KV-event-fed vs approximate prompt-hash)
with the pull on or off, `minCachedTokenDelta: 16384` (from the
crossover below). The workload replays recorded agentic traces (the
SemiAnalysis Weka corpus, agent chains included) with aiperf at
concurrencies 32/64/128, ~15 minutes per cell, single run per cell, zero
request errors in every completed cell. Arm configurations are the
`epp-glm-*.yaml` files in [../benchmarking/](../benchmarking/README.md).

## Pull versus recompute (single request)

Single source-consumer engine pair on different pods, fresh prefix seeded on
the source, TTFT measured on the consumer with and without the pull, warmed
transfer mesh, single rep per point. Every pull verified byte-exact against
the consumer's `kv_offload_load_bytes_total` (loaded bytes = tokens x ~93 KB
within 1%):

| prefix tokens | recompute | P2P pull | delta |
|---|---|---|---|
| 8,070 | 1.00 s | 1.69 s | +69% |
| 13,648 | 1.74 s | 1.76 s | tie |
| 21,617 | 2.76 s | 1.80 s | -35% |
| 34,214 | 4.51 s | 2.51 s | -44% |
| 48,109 | 6.38 s | 1.98 s | -69% |
| 65,111 | 8.78 s | 1.98 s | -78% |
| 98,220 | 13.75 s | 2.29 s | -83% |

The pull's latency is nearly flat (~1.7-2.3 s: session floor plus ~4.5 GB/s
effective transfer) while recompute pays ~130-144 us per token, so the
crossover is a dead tie at 13,648 tokens and the gap past it widens without
bound. `minCachedTokenDelta: 16384` sits just above the measured tie, so
fired pulls are always in the win region.

Two measurement controls worth repeating on any rig: the identical sweep
*without* the sidecar's injected source-pull `kv_transfer_params` block
never pulls (pull time
equals recompute time, zero bytes loaded) - the engine does not fetch from
peers on its own; the router/sidecar directive is the trigger. And the
*first* pull between a fresh pod pair pays a one-time ~6 s
session-establishment cost that steady-state pulls never see - calibrate on
a warmed pair or the transient reads as the pull's cost.

## Agentic traces across the concurrency ladder (four arms)

TTFT p50 / p90 (ms) per cell; the pull's delta against the same placement
without it in parentheses:

| conc | approx | approx + P2P | precise | precise + P2P |
|---|---|---|---|---|
| 32 | 1,665 / 4,095 | 1,621 / 3,917 | 2,265 / 7,557 | 1,649 (-27%) / 4,136 (-45%) |
| 64 | 2,234 / 4,897 | 2,276 / 5,449 | 2,801 / 9,823 | 2,581 (-8%) / 7,139 (-27%) |
| 128 | 2,963 / 9,226 | 2,953 / 8,833 | 3,802 / 11,755 | 3,177 (-16%) / 9,970 (-15%) |

Reading the arms:

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
