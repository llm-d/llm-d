# openai/gpt-oss-120b P2P KV Cache Sharing Benchmark on vLLM (H200)

The benchmark runs `openai/gpt-oss-120b` (MXFP4) aggregated, one H200 per
pod (TP=1), ~1.22M tokens of GPU KV per pod (measured from the engine
startup log at `--gpu-memory-utilization=0.85`, `--max-model-len=65536`),
an 88 GiB CPU offload tier per pod (~1.8x the GPU KV cache), vLLM block
size 64, KV transfers over NIXL. Routing uses the llm-d
inference gateway with the precise (KV-event-fed) prefix index; the P2P arm
adds the `p2p-source-producer` with `minCachedTokenDelta: 2048`. The
document Q&A and the pool scenarios both ran on 16 pods. Workload
profiles, EPP arm configurations, and the run protocol are in
[../benchmarking/README.md](../benchmarking/README.md).

Provenance note on index sizing: the precise index's `podCacheSize` must
cover every legitimate (endpoint, tier) holder per hot block - the
shipped configurations set 32 for this 16-pod GPU+CPU fleet. The document
Q&A table is measured at 32. The uniform-pool and hot-set tables predate
that setting; correct sizing improves affinity placement most, so their
arm ordering (affinity ahead) is the direction sizing favors and stands.
The pull-versus-recompute ladder bypasses the index entirely and is
unaffected.

## Pull versus recompute (single request)

Single source-consumer pod pair, fresh prefix seeded on the source, prefill
latency measured on a cold consumer, 5-rep medians. Two pods only, with no
sidecar and no EPP - the driver injects the pull parameters directly, so an
inert configuration cannot be mistaken for a measurement.

The canonical numbers are from the run on the fixed stack (engine
`nightly-1240c74c`, P2P code `#48021` as merged plus in-review robustness
patches).
The `superseded` column is an earlier run of the same method on the
pre-fix stack and is kept only so the two are not confused where the older
figures are still quoted elsewhere.

| prefix tokens | recompute | P2P pull | delta | superseded (2026-07-17) |
|---:|---:|---:|---:|---:|
| 2,048 | 78.3 ms | 34.6 ms | **-55.8%** | -31% |
| 8,192 | 249.9 ms | 56.5 ms | **-77.4%** | -42% |
| 16,384 | 510.3 ms | 85.9 ms | **-83.2%** | -54% |
| 32,768 | 1,173.2 ms | 165.2 ms | **-85.9%** | -62% |
| 49,152 | 1,987.7 ms | 235.0 ms | **-88.2%** | -68% |

<img src="./gptoss-crossover.png" width="900" alt="Prefill latency versus prefix length, recompute versus P2P pull; the pull is lower at every measured length, reaching -88% at 48K tokens">

The pull wins at every measured length and the gap grows with the prefix,
because pull time is nearly flat in prefix size (34.6 / 56.5 / 85.9 /
165.2 / 235.0 ms) while recompute is linear (78.3 / 249.9 / 510.3 /
1,173.2 / 1,987.7 ms). The smallest winning length sets the router's
`minCachedTokenDelta: 2048`.

The same method run against the pre-fix stack agreed with the fixed one
within ~6% at 2K and within 1% from 8K up, so this ladder is a property of
the transport and the model, not of the engine build.

## Document Q&A (the headline)

192 conversations, each with a private 48K-token document prefix, 6 short
questions (256-token answers), 128 conversations concurrent, 1,152 turns per
run. The ~9.2M-token corpus fits inside the fleet's aggregate GPU KV
(16 pods x ~1.22M tokens/pod ~= 19.5M) with room to spare, so the
displacement here is not capacity scarcity.

Measured on 16 pods with the shipped `podCacheSize: 32`, each arm
cold-rolling the fleet before its first run so arms cannot contaminate
each other, then run twice. TTFT p50 / p95 / p99 (s); throughput
(turns/s):

| arm | run | ok/fail | p50 | p95 | p99 | turns/s |
|---|---|---|---:|---:|---:|---:|
| Precise prefix routing | 1 (cold) | 864/48 | 0.2 | 83.3 | 162.8 | 3.93 |
| Precise prefix routing | 2 (warm) | 1152/0 | 0.3 | 14.0 | 25.2 | 10.15 |
| Precise + P2P | 1 (cold) | 870/47 | 0.2 | 85.7 | 165.4 | 3.90 |
| Precise + P2P | 2 (warm) | 1152/0 | 0.3 | 10.1 | 18.6 | 11.90 |
| **Load-aware + P2P** | 1 (cold) | 1152/0 | 1.7 | **11.4** | **20.7** | **11.34** |
| **Load-aware + P2P** | 2 (warm) | 1152/0 | 0.6 | **9.0** | **16.6** | **13.66** |

Zero pod restarts during the runs (one pod hung at NIXL/UCX backend init
during an arm's cold roll and was replaced before the run started).

**Load-aware + P2P wins this scenario**: +35% throughput and 1.5x better
p99 TTFT than precise routing warm (16.6 s vs 25.2 s), and on a cold fleet
the separation is stark - 1152/1152 with zero failures at 2.9x the
throughput and 7.9x better p99, while both affinity arms shed 47-48
requests to the client timeout. The trade is visible in the medians:
affinity's warm p50 is 0.3 s (a local cache hit) against load-aware +
P2P's 0.6 s (every displaced question pays a pull); load-aware + P2P
buys the tail and the throughput with it.

**Precise + P2P versus precise alone reads +17% throughput and -26% p99
warm - suggestive, not established.** One warm run per arm, and the
throughput delta sits inside this workload's 10-28% run-to-run spread.
The structural expectation is limited engagement: affinity keeps the KV
local, so `minCachedTokenDelta` is rarely met and there is little to
fetch. Per-arm session counters do not survive the per-arm cold roll, so
this pair needs repeated runs with mechanism counters before the pull is
credited with a throughput role under affinity placement.

**The affinity arms are cold-start fragile, and that is what the cold rows
show.** On a cold fleet every endpoint scores identically, so precise
affinity has no signal to separate candidates and the pick collapses onto
one pod: sampling in-flight depth during a cold affinity run found 122/128
requests in flight with one pod holding 78.7% of them and six pods idle. It
disperses within a minute as the prefix index fills, but the tail damage is
done - that is the 165 s p99 and the 47-48 client timeouts. Load placement
spreads by construction and never sees it.

<img src="./gptoss-docqa.png" width="900" alt="Document Q&A across three routing arms, cold and warm: affinity arms collapse to 162.8-165.4 s p99 cold with client timeouts, load-aware plus P2P holds 20.7 s cold and 16.6 s warm at 13.66 turns/s">

Index sizing dominates every arm that consults the index. The same
campaign with the precise index at its default `podCacheSize` (undersized
for a 16-pod GPU+CPU fleet) reads warm 4.65 / 5.06 / 7.54 turns/s across
the three arms and a 7.3x p99 separation: undersizing cripples affinity
placement most (it evicts the very holders placement routes on, p99
132.6 s against 25.2 s here) and degrades load-aware + P2P through P2P
source selection (7.54 against 13.66). Undersizing therefore exaggerates
the arm separation without changing the ordering - size the index before
comparing arms.

## Uniform shared-prefix pool (three arms)

128 shared prefixes x 48K tokens (~5x one pod's GPU cache), 256-token
questions, 64-token outputs, constant-rate stages, `rdma/ib` on every pod.
Achieved rate (req/s) / request latency p50:

| offered | Affinity | Load, no P2P | Load + P2P |
|---|---|---|---|
| 6 req/s | 5.97 / 0.50 s | 5.59 / 5.6 s | 5.96 / 0.64 s |
| 12 req/s | 11.92 / 0.49 s | 9.02 / 26.2 s | 11.49 / 0.98 s |
| 18 req/s | 17.87 / 0.48 s | 8.58 / 45.7 s | 17.46 / 0.67 s |
| 24 req/s | 23.82 / 0.48 s | 9.01 / 63.4 s | 21.93 / 0.70 s |
| 30 req/s | 29.76 / 0.48 s | 9.21 / 81.2 s | 29.19 / 0.73 s |

A uniform pool is affinity's best case and it is near-ideal here: flat
sub-half-second p50 through 30 req/s (affinity + P2P matches it within
noise; the pull idles under affinity placement). The recompute control
saturates near 9 req/s - every cross-pod placement re-prefills 48K tokens.
The pull sets load placement's floor: load + P2P tracks offered rate
through 30 req/s at sub-second p50, +143% over the recompute floor at rate
24 (21.93 vs 9.01 req/s; 0.70 s vs 63.4 s p50) and +217% at rate 30, on
120 reusable P2P sessions established, with 210M external-hit tokens
served from the offload tier (a figure that includes local CPU restores
alongside peer transfers). Affinity stays ahead by a
constant factor (0.48 vs 0.73 s) because scattering pays transfer work
affinity never pays; zero failures and zero restarts in all arms.

## Hot set (the payoff case)

48K-token prefixes, 512-token outputs, rates past what the fleet can
absorb on recompute. The hot set must exceed one pod's GPU KV (~1.22M
tokens here) for this scenario to measure anything: at 8 prefixes (0.31x)
the set fits in every pod and no arm ever recomputes. Measured at 64
prefixes (3.07M tokens, 2.5x a pod's cache). Achieved rate (req/s) / TTFT
p50 / request latency p50:

| offered | Affinity | Load, no P2P | Load + P2P |
|---|---|---|---|
| 12 req/s | 11.94 / 188 ms / 0.30 s | 9.31 / 7.9 s / 16.6 s | 11.84 / 310 ms / 0.42 s |
| 24 req/s | 23.04 / 183 ms / 0.31 s | 11.47 / 24.3 s / 34.5 s | 22.83 / 271 ms / 0.42 s |
| 36 req/s | 34.03 / 190 ms / 0.36 s | 11.77 / 47.0 s / 61.6 s | 34.34 / 249 ms / 0.45 s |
| 48 req/s | 46.03 / 196 ms / 0.38 s | 13.85 / 58.2 s / 72.5 s, 274 failures | 44.93 / 254 ms / 0.48 s, 0 failures |

Same placement, the pull as the only variable: at offered 48 the recompute
floor caps at 13.85 req/s and sheds 274 requests to the client timeout,
while the pull arm holds 44.93 req/s at 254 ms with none - +224%
throughput on 120 P2P sessions and 7.5 TB served from the offload tier.
Affinity is not the arm that suffers at this prefix count (64 prefixes
over 16 pods spreads ownership ~4 per pod); owner concentration needs a
prefix count below the pod count, which is also a set small enough to fit
everywhere, so the two pathologies do not co-exist in one workload.
