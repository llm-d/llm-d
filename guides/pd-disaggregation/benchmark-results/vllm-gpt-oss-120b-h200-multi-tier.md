# GPT-OSS-120B P/D Multi Tier KV Cache on H200

This report compares plain NIXL P/D with CPU KV offloading. Neither arm uses a
filesystem KV tier.

## Testbed

- Cluster: CoreWeave Kermit for the random and document-Q&A workloads;
  CoreWeave Fozzie for the eviction-pressure workload.
- Model: `openai/gpt-oss-120b`.
- Engine: vLLM v0.27.1.
- Topology: 8 TP=1 prefills on one 8x H200 node and 2 TP=4 decoders on a
  second 8x H200 node.
- P/D transport: `NixlConnector` over the CoreWeave RDMA resource.
- Engine block size and precise router block size: 128 tokens.
- Model loading: `runai_streamer`, distributed loading with concurrency 16,
  and a persistent `VLLM_CACHE_ROOT` host volume.
- One measured run per arm. Treat small differences as directional, not as a
  stable performance estimate.

Both arms use the same precise prefill placement: prefix score weight 3, queue
weight 2, KV-utilization weight 2, and decode active-request weight 2.

| Arm | Engine tiers and policy | Router difference |
| --- | --- | --- |
| PD NIXL | HBM; NIXL P/D handoff | controlled precise |
| PD Multi Tier | HBM + 100 GiB CPU on each prefiller; decode is NIXL only | none |

## Workload 1: checked-in P/D guide profile

`guide_pd-disaggregation_1.yaml` sends 5,400 random requests at 45 QPS over a
120-second window. Every request has 5,000 input tokens and requests 250 output
tokens. Random prompts intentionally provide little reusable prefix state.

| Arm | OK/fail | Request/s | Mean latency | P95 latency | P99 latency | Mean TTFT | P95 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PD NIXL | 5,400/0 | 44.27 | 2.355 s | 2.900 s | 3.719 s | 0.518 s | 1.052 s |
| PD Multi Tier | 5,400/0 | 44.35 | 2.361 s | 2.899 s | 3.608 s | 0.521 s | 1.038 s |

CPU-only Multi Tier is effectively tied with plain NIXL: mean latency is 0.25%
higher and mean TTFT is 0.48% higher.

## Workload 2: document Q&A

`guide_p2p-kv-cache-sharing_1.yaml` schedules 1,152 turns across 192 private
documents. Each conversation has a 49,152-token document prefix, six turns,
256 new input tokens per turn, requested 256-token output, concurrency 128,
and a 180-second request timeout. Reuse is private to each conversation rather
than one global shared prefix.

| Arm | OK/fail | Request/s | Mean latency | Median latency | P95 latency | P99 latency | Mean TTFT | Median TTFT | P95 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PD NIXL | 1,133/19 | 4.303 | 13.947 s | 5.590 s | 72.955 s | 159.100 s | 11.282 s | 2.827 s | 70.646 s |
| PD Multi Tier | 1,117/35 | 4.305 | 16.657 s | 6.481 s | 104.042 s | 176.812 s | 13.882 s | 3.447 s | 101.837 s |

Compared with plain NIXL, CPU-only Multi Tier has 19.4% higher mean latency,
23.1% higher mean TTFT, and 16 fewer successes.

Successful output length varied across runs despite the same seeded workload:
mean output tokens were 619 for plain NIXL and 625 for CPU Multi Tier. Median
output stayed 251-252 tokens. This is another reason not to over-interpret
one-run mean differences.

## Workload 3: eviction pressure beyond HBM capacity

[`tiered-eviction.yaml.in`](../benchmark-templates/tiered-eviction.yaml.in)
schedules 10,800 requests over eight 60-second Poisson stages from 5 to 40
QPS. It uses 1,000 prefix groups, five prompts per group, a 16,000-token shared
prefix, 256 new input tokens, and 256 requested output tokens. Multi-turn chat
is disabled.

This workload was calibrated from live vLLM capacity rather than nominal GPU
memory. Each TP=1 prefiller reported 1,630,790 HBM KV tokens, or 13,046,320
tokens across eight prefills. The approximate unique prefill working set is
17,280,000 tokens, 1.32 times aggregate HBM capacity. The CPU arm adds 100 GiB
per prefiller, 800 GiB total. Neither arm uses a filesystem KV tier.

Both Fozzie arms used the same model, vLLM image, 8x TP=1 prefill plus 2x TP=4
decode topology, NIXL/RDMA P/D transport, 128-token engine and router block
size, and corrected DP-aware precise router configuration. The only engine
change was HBM-only NIXL versus NIXL plus the prefill CPU tier. The router was
restarted between arms to clear the precise cache index.

| Arm | OK/fail | Request/s | Measured time | Mean latency | P90 latency | Mean TTFT | P90 TTFT | Mean TPOT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PD NIXL | 10,800/0 | 10.699 | 1,009.45 s | 8.120 s | 20.705 s | 6.405 s | 19.112 s | 6.677 ms |
| PD Multi Tier | 10,799/1 | 11.650 | 926.98 s | 2.365 s | 2.869 s | 0.512 s | 0.840 s | 7.223 ms |

| Target QPS | NIXL request/s | Multi Tier request/s | NIXL P90 latency | Multi Tier P90 latency | NIXL P90 TTFT | Multi Tier P90 TTFT |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 4.60 | 5.17 | 2.163 s | 2.091 s | 1.007 s | 0.968 s |
| 10 | 9.61 | 9.73 | 2.093 s | 2.034 s | 0.813 s | 0.818 s |
| 15 | 15.14 | 14.72 | 2.332 s | 2.248 s | 0.908 s | 0.747 s |
| 20 | 18.75 | 19.69 | 3.144 s | 2.143 s | 1.552 s | 0.669 s |
| 25 | 24.33 | 24.29 | 4.631 s | 2.447 s | 2.790 s | 0.706 s |
| 30 | 25.40 | 29.72 | 7.374 s | 2.700 s | 5.514 s | 0.776 s |
| 35 | 24.20 | 33.43 | 17.721 s | 3.019 s | 15.988 s | 0.959 s |
| 40 | 22.44 | 35.43 | 37.859 s | 3.448 s | 36.271 s | 1.632 s |

Plain NIXL peaks at 25.40 request/s in the 30-QPS stage, then declines as
HBM-only prefix eviction drives repeated prefill work. Multi Tier sustains
33.43 request/s at 35 QPS and 35.43 request/s at 40 QPS. Across the full run,
Multi Tier improves successful request rate by 8.9%, shortens measured
completion time by 8.2%, and reduces mean TTFT by 92.0%. Its mean TPOT is 8.2%
higher, so CPU restore overhead remains visible after the first token.

The CPU counters started at zero. During the measured arm, prefills restored
3.52 TiB from CPU to GPU in 7,055 operations and wrote 1.51 TiB from GPU to CPU
in 4,738 operations. This verifies that the result exercises CPU retention and
restore rather than only allocating an unused tier. The one failure occurred
in the 40-QPS stage, a 0.009% overall failure rate. One run per arm is not
enough to claim a stable percentage; repeat the comparison before using the
delta as a capacity target.

Inference-perf flagged output-token count mismatches on 8,811 NIXL requests
and 8,758 Multi Tier requests. Its aggregate output-token totals still matched
256 tokens per successful request, but the mismatch makes TPOT less reliable
than request rate, completion time, and TTFT for this comparison.

## Mechanism evidence

| Workload | CPU to GPU | Interpretation |
| --- | ---: | --- |
| Random guide workload | 0 GiB | no reuse path engaged |
| Document Q&A | about 201 GiB | local CPU restores occurred |
| Eviction pressure | 3.52 TiB | sustained local CPU restores beyond HBM capacity |

## Conclusion

For this checked-in P/D topology, CPU Multi Tier is nearly free on the random
saturation profile, does not improve document Q&A, and strongly improves the
deliberate 1.32x-HBM eviction-pressure profile. The value is workload-specific:
the reusable working set must exceed HBM capacity, fit in CPU memory, and be
revisited enough for CPU restore to beat recomputation. Plain NIXL has the best
document-Q&A success count and mean latency in these single runs.

Use the CPU-only overlay as the primary Multi Tier guide comparison. Repeat the
two arms before treating any percentage as a stable performance estimate.
