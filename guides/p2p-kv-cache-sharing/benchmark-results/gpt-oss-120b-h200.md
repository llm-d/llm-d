# openai/gpt-oss-120b P2P KV Cache Sharing Benchmark on vLLM (H200)

The benchmark runs `openai/gpt-oss-120b` (MXFP4) aggregated, one H200 per
pod (TP=1), ~1.22M tokens of GPU KV per pod (measured from the engine
startup log at `--gpu-memory-utilization=0.85`, `--max-model-len=65536`),
an 88 GiB CPU offload tier per pod (~1.8x the GPU KV cache), vLLM block
size 64, KV transfers over NIXL. Routing uses the llm-d
inference gateway with the precise (KV-event-fed) prefix index; the P2P arm
adds the `p2p-source-producer` with `minCachedTokenDelta: 2048`. The
document Q&A scenario ran on 14 pods; the pool scenarios on 16. Workload
profiles, EPP arm configurations, and the run protocol are in
[../benchmarking/README.md](../benchmarking/README.md).

## Pull versus recompute (single request)

Single source-consumer pod pair, fresh prefix seeded on the source,
prefill latency measured on a cold consumer, 5-rep medians:

| prefix tokens | recompute | P2P pull | delta |
|---|---|---|---|
| 2,048 | 70.6 ms | 49.0 ms | -31% |
| 8,192 | 205.4 ms | 120.1 ms | -42% |
| 16,384 | 426.3 ms | 196.2 ms | -54% |
| 32,768 | 983.0 ms | 376.3 ms | -62% |
| 49,152 | 1,695 ms | 550.5 ms | -68% |

<img src="./gptoss-crossover.png" width="900" alt="Prefill latency versus prefix length, recompute versus P2P pull">

The pull wins at every measured length and the gap grows with the prefix;
the smallest winning length sets the router's `minCachedTokenDelta: 2048`.

## Document Q&A (the headline)

192 conversations, each with a private 48K-token document prefix, 6 short
questions (256-token answers), 128 conversations concurrent. The
~9.2M-token corpus fits inside the fleet's aggregate GPU KV (14 pods x
~1.22M tokens/pod ~= 17M) with room to spare - so the displacement here is
not capacity scarcity. The likelier driver: the baseline's composite
scorer (prefix-cache weight 3, queue weight 2, kv-util weight 2,
no-hit-lru weight 2) trades a document's cache locality for load-balancing
under 128 concurrent sessions spread across only 14 owner pods, so a burst
of turns can still queue behind an overloaded owner or land on a colder
pod that recomputes. This is consistent with the P2P arm moving 30-32M
prefix tokens per run - more than 3x the corpus - implying repeated
cross-pod placement rather than one-time cold misses. Load-aware placement
plus the pull removes the tradeoff: every question goes to whichever pod
is least loaded, and that pod pulls the prefix instead of recomputing or
queueing for it. Two full runs with arm order alternated; all four runs
completed 1,152/1,152 turns with zero errors and zero restarts.

TTFT p50 / p95 / p99 (s); throughput (turns/s):

| run | Precise prefix routing | Load-aware + P2P |
|---|---|---|
| 1 | 4.1 / 41.0 / 80.5; 5.98 | 4.5 / 13.0 / 20.9; 7.02 |
| 2 (order reversed) | 4.2 / 17.3 / 37.2; 7.66 | 3.9 / 12.5 / 26.7; 7.76 |

<img src="./gptoss-docqa.png" width="900" alt="Document Q&A TTFT percentiles and throughput across two order-alternated runs">

Medians are equal; the arms separate on tails and stability: p99 TTFT
21-27 s versus 37-81 s, up to +17% throughput, and 10% run-to-run spread
versus 28%. The P2P arm moved 30-32M prefix tokens between pods per run.

## Uniform shared-prefix pool (three arms)

128 shared prefixes x 48K tokens (~5x one pod's GPU cache), 256-token
questions, 64-token outputs, constant-rate stages. Achieved rate (req/s) /
request latency p50 (s):

| offered | Affinity | Load, no P2P | Load + P2P |
|---|---|---|---|
| 8 req/s | 7.9 / 0.7 | 6.7 / 6.5 | 7.7 / 1.6 |
| 12 req/s | 11.9 / 0.7 | 9.0 / 24.9 | 11.4 / 2.3 |
| 16 req/s | 15.8 / 0.7 | 8.8 / 37.3 | 15.1 / 3.6 |
| 24 req/s | 23.7 / 0.8 | 9.4 / 63.5 | 16.7 / 30.0 |

A uniform pool is affinity's best case and it is near-ideal here. The
recompute control saturates near 9.4 req/s; the pull recovers most of the
spread penalty (+78% over recompute at saturation, on ~139M pulled tokens).

## Hot set (the payoff case)

8 shared prefixes x 48K tokens, 512-token outputs, rates past what the 8
owner pods can absorb. Achieved rate (req/s) / latency p50 (s) / failures:

| offered | Affinity (8 owners) | Load + P2P (16 pods) |
|---|---|---|
| 24 req/s | 14.7 / 27.0 / 0 | 20.9 / 9.6 / 0 |
| 36 req/s | 15.3 / 53.8 / 0 | 29.1 / 15.9 / 0 |
| 48 req/s | 13.1 / 75.3 / 672 | 34.3 / 26.0 / 0 |

The owners cap near 15 req/s and shed 672 requests to the client timeout;
load-aware placement plus the pull serves the same hot content from the
whole fleet at 2.6x the throughput with zero failures.
