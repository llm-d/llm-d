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

All three Fozzie arms used the same model, vLLM image, 8x TP=1 prefill plus 2x
TP=4 decode topology, NIXL/RDMA P/D transport, 128-token engine and router
block size, and precise router scorer weights. The router and all engines were
restarted between arms to clear the precise index, HBM cache, and CPU tier.
The controlled arms separate the engine and placement effects:

| Arm | Prefill engine | Precise CPU backend weight | Isolated effect |
| --- | --- | ---: | --- |
| PD NIXL | `NixlConnector` | 0.0 | HBM-only baseline |
| CPU offload, CPU-blind | `MultiConnector` with NIXL and 100 GiB CPU | 0.0 | CPU retention and engine-local restore |
| PD Multi Tier, CPU-aware | Same byte-identical engine as CPU-blind | 0.4 | CPU-aware placement |

| Arm | OK/fail | Request/s | Measured time | Mean latency | P90 latency | Mean TTFT | P90 TTFT | Mean TPOT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PD NIXL | 10,800/0 | 10.598 | 1,019.07 s | 9.100 s | 24.010 s | 7.404 s | 22.324 s | 6.573 ms |
| CPU offload, CPU-blind | 10,800/0 | 11.325 | 953.68 s | 4.144 s | 8.078 s | 2.288 s | 5.950 s | 7.211 ms |
| PD Multi Tier, CPU-aware | 10,800/0 | 11.466 | 941.90 s | 2.390 s | 2.859 s | 0.491 s | 0.846 s | 7.375 ms |

| Target QPS | NIXL request/s | CPU-blind request/s | CPU-aware request/s | NIXL P90 latency | CPU-blind P90 latency | CPU-aware P90 latency |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 4.81 | 5.11 | 4.59 | 2.050 s | 2.034 s | 2.061 s |
| 10 | 9.10 | 9.84 | 9.13 | 2.149 s | 2.174 s | 2.133 s |
| 15 | 14.01 | 14.23 | 14.41 | 2.381 s | 2.113 s | 2.121 s |
| 20 | 18.52 | 19.22 | 19.63 | 3.217 s | 2.569 s | 2.259 s |
| 25 | 23.18 | 22.99 | 24.01 | 4.576 s | 3.779 s | 2.580 s |
| 30 | 26.52 | 27.88 | 29.15 | 8.491 s | 5.952 s | 2.652 s |
| 35 | 23.91 | 29.53 | 32.94 | 23.027 s | 7.377 s | 3.173 s |
| 40 | 22.10 | 32.62 | 36.04 | 40.267 s | 12.477 s | 3.511 s |

The CPU-blind arm shows that the engine tier itself provides most of the
capacity gain over plain NIXL. It improves full-run successful request rate by
6.9%, reduces mean request latency by 54.5%, and reduces P90 request latency by
66.4%. The CPU-aware arm then isolates the placement algorithm: compared with
the byte-identical CPU-blind engine, it reduces mean request latency by 42.3%
and P90 request latency by 64.6%. At the 40-QPS stage it increases achieved
request rate from 32.62 to 36.04 request/s and reduces P90 latency from 12.477
to 3.511 seconds.

The overall request/s difference understates the latency improvement because
the profile contains a fixed 60-second gap between stages. Per-stage request
rate and the queued-work tail expose the saturation behavior. Plain NIXL
declines after 30 QPS, the CPU-blind engine extends the saturation knee, and
CPU-aware placement sustains the highest rate through 40 QPS.

During the CPU-aware arm, periodic vLLM counters across the eight prefills
logged 3.58 TiB of CPU-to-GPU loads and 1.54 TiB of GPU-to-CPU stores, with
zero CPU allocation failures. These are aggregate transfer activities, not a
unique KV footprint. They verify that CPU-aware placement exercised retention
and restore rather than only allocating an unused tier.

Inference-perf flagged output-token count mismatches on 8,746 NIXL requests,
8,741 CPU-blind requests, and 8,715 CPU-aware requests. Aggregate output-token
totals still matched 256 tokens per successful request, but the mismatch makes
TPOT less reliable than request rate, completion time, and TTFT for this
comparison. Each arm is a single run and inference-perf generated a different
seed for each run, so repeat the comparison before treating the percentages as
a stable capacity estimate.

## Mechanism evidence

| Workload | CPU to GPU | Interpretation |
| --- | ---: | --- |
| Random guide workload | 0 GiB | no reuse path engaged |
| Document Q&A | about 201 GiB | local CPU restores occurred |
| Eviction pressure | 3.58 TiB | sustained local CPU restores beyond HBM capacity |

## Conclusion

For this checked-in P/D topology, CPU Multi Tier is nearly free on the random
saturation profile, does not improve document Q&A, and strongly improves the
deliberate 1.32x-HBM eviction-pressure profile. The value is workload-specific:
the reusable working set must exceed HBM capacity, fit in CPU memory, and be
revisited enough for CPU restore to beat recomputation. Plain NIXL has the best
document-Q&A success count and mean latency in these single runs.

Use the NIXL plus CPU overlay as the primary Multi Tier guide comparison. The
controlled eviction test shows separate gains from the CPU tier and from
CPU-aware placement. Repeat all three arms before treating any percentage as a
stable performance estimate.
