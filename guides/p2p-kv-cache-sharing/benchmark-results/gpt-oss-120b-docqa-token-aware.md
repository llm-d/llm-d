# openai/gpt-oss-120b document Q&A with token-aware placement (H200, TCP)

This report runs the guide's document Q&A workload profile across three
placements, each with and without the `p2p-source-producer`: token-aware
(`epp-tokenaware(-p2p).yaml`), load-aware (`epp-load(-p2p).yaml`) and
affinity (the shipped `router/p2p-kv-cache-sharing.values.yaml` and its
no-pull control). Placement configs are the files in this guide, unchanged
except for the render Service URL.

## Setup

* 8 x `openai/gpt-oss-120b` TP=1 on H200, vLLM `v0.27.1`, `OffloadingConnector`
  CPU tier 88 GiB per pod with the P2P secondary tier, `self_describing_kv_events: true`.
* The pods have no RDMA device, so NIXL/UCX runs the pull over TCP. The
  calibration recipe returns `minCachedTokenDelta: 2048` on this transport:
  the pull won at every tested length (52 ms against 85 ms at 2,048 tokens,
  533 ms against 1,102 ms at 32,768).
* Workload: the guide's `guide_p2p-kv-cache-sharing_1.yaml` profile
  ([llm-d-benchmark#1656](https://github.com/llm-d/llm-d-benchmark/pull/1656)
  at the pinned commit) run through the llm-d-benchmark harness image
  `v0.7.0`, scaled from 16 to 8 pods: 96 conversations instead of 192 and
  64 requests in flight instead of 128, 576 requests per run. Each
  conversation carries a private 48K-token document and asks 6 questions of
  256 tokens with 256-token answers. The client ran 64 inference-perf
  workers instead of 8; worker count sets client parallelism, not the
  workload.
* Every run starts from empty caches: each engine's GPU cache and CPU tier
  are reset (`POST /reset_prefix_cache?reset_external=true` with
  `VLLM_SERVER_DEV_MODE=1`) and the router index follows through the
  engines' clear events.

## Three placements, with and without the pull

One run per arm, seed 37. Metrics are inference-perf's summary for the run;
TPOT excludes the first token.

| Placement | Pull | Pulls | Served req/s | TTFT p50 / p90 ms | TPOT p50 / p90 ms | Request latency p50 / p90 s |
| --- | --- | --- | --- | --- | --- | --- |
| token-aware | yes | 93 | **6.03** | 609 / **7,191** | 11.3 / **33.2** | 5.3 / 21.4 |
| token-aware | no | 0 | 5.02 | 1,758 / 7,221 | 20.8 / 45.8 | 8.7 / 20.9 |
| load-aware | yes | 343 | 4.26 | 2,993 / 8,882 | 13.4 / 51.2 | 9.0 / 19.6 |
| load-aware | no | 0 | 2.75 | 3,555 / 8,840 | 32.9 / 97.4 | 13.9 / 32.0 |
| affinity (shipped default) | yes | 36 | 2.75 | 528 / 14,054 | 7.9 / 139.4 | 5.0 / 75.3 |
| affinity (shipped default) | no | 0 | 2.66 | 637 / 14,624 | 8.1 / 110.5 | 5.7 / 74.7 |

* In this single run token-aware + P2P serves 42% more than load-aware +
  P2P and 2.2x the shipped affinity default, with half of affinity's TTFT
  p90. Four runs each of the token-aware and load-aware arms follow.
* Affinity's median TTFT and TPOT are the lowest because most requests land
  on lightly used pods; its tail sits on owner pods that queue, and those
  runs take twice as long to serve the same requests. The pull barely fires
  under affinity (36 pulls) and does not change that.
* Load-aware placement depends on the pull on this workload: +55% served
  throughput with it.

## Load-aware vs token-aware placement, four runs each

Seeds 37, 101, 102 and 103 for every arm; the same seed across arms within a
seed, empty caches before every run. Means of the four runs.

| Placement | Pull | Served req/s | TTFT p50 / p90 s | TPOT p90 ms | Request latency p50 s | Pulls per run |
| --- | --- | --- | --- | --- | --- | --- |
| token-aware | yes | **6.28** | **0.67 / 7.06** | **33.8** | **5.55** | 89 |
| token-aware | no | 5.46 | 0.90 / 7.18 | 39.8 | 7.55 | 0 |
| load-aware | yes | 4.43 | 2.98 / 9.17 | 49.4 | 8.69 | 344 |
| load-aware | no | 2.80 | 3.56 / 8.33 | 92.8 | 13.90 | 0 |

* Token-aware placement is the larger effect: without the pull it already
  beats load-aware placement with the pull on every metric above.
* The pull helps both placements on throughput, TPOT and median latency.
  Under load-aware placement it raises TTFT p90 (9.17 s against 8.33 s, in
  all four runs): load-aware placement ignores the cache, so it pulls on
  most requests, and pulls queued behind slow TCP transfers land in the
  tail. Under token-aware placement the pull fires a quarter as often and
  TTFT p90 does not rise.

## Token-aware placement: the pull's own margin

Four runs of each arm (seeds 37, 101, 102, 103); the same seed across arms
within a seed, empty caches before every run. Mean, with the four runs in
brackets.

| Metric | No pull | With pull | Change |
| --- | --- | --- | --- |
| Served req/s | 5.46 (5.02, 5.56, 5.94, 5.33) | 6.28 (6.03, 6.52, 6.31, 6.26) | +15% |
| TTFT p50 ms | 900 (1,758, 610, 520, 710) | 670 (609, 580, 770, 710) | -26% |
| TTFT p90 ms | 7,180 (7,221, 7,240, 7,030, 7,220) | 7,060 (7,191, 6,844, 7,128, 7,098) | -2% |
| TPOT p90 ms | 39.8 (45.8, 41.6, 35.1, 36.8) | 33.8 (33.2, 35.4, 33.8, 32.9) | -15% |
| Request latency p50 s | 7.55 (8.71, 7.31, 6.90, 7.28) | 5.55 (5.32, 5.25, 5.88, 5.77) | -26% |

The pull shortens decode for everyone on the pod (a recompute spends prefill
on a GPU that is decoding for other requests) and cuts median TTFT and
request latency by a quarter. TTFT p90 is set by each conversation's first
turn, a cold 48K-token prefill that no pull can serve, so it barely moves.

## Caveats

* One model on one 8-pod fleet over TCP. With RDMA the transfer is about 4x
  faster per token, so the pull's margin should be larger.
* The affinity arms are one run each; the token-aware and load-aware arms
  are four runs each.
