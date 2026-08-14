# GLM-5.2-FP8 DP P/D Multi Tier on H200

This report compares precise NIXL P/D with precise CPU Multi Tier under data
parallelism. Neither arm uses a filesystem KV tier.

## Testbed

- Cluster: CoreWeave Piggy.
- Model: `zai-org/GLM-5.2-FP8`.
- Engine image: `quay.io/niliguy/vllm-openai:nightly-6f91edf9-pr50302`.
- Topology: one TP=1, DP=8 prefill pod on an 8x H200 node and one TP=1, DP=8
  decode pod on a second 8x H200 node.
- P/D transport: `NixlConnector` over the CoreWeave RDMA resource.
- Engine and precise-router block size: 64 tokens.
- Router: the same DP-aware precise configuration in both arms, including
  `precise-prefix-cache-producer`, CPU backend weight 0.4,
  `prefix-cache-affinity-filter`, and `token-load-scorer`.
- Model loading: `runai_streamer`, distributed loading with concurrency 16,
  and persistent `VLLM_CACHE_ROOT` storage.
- One measured run per arm, in NIXL-then-Multi-Tier order.

Both pods exposed one ZMQ publisher per DP rank. Ports `5557-5564` were
reachable on each pod before traffic started, and the router reported 16
active KV-event subscribers and 16 ready endpoints. Per-rank request counters
and KV metrics were collected every two seconds.

The controlled connector difference was:

| Arm | Prefill KV configuration | Decode KV configuration |
| --- | --- | --- |
| Precise PD NIXL | `NixlConnector`; HBM only | `NixlConnector`; HBM only |
| Precise PD Multi Tier | `MultiConnector`: NIXL plus CPU `OffloadingConnector` | `NixlConnector`; HBM only |

The CPU connector used `offload_prompt_only: true`, LRU eviction, and
`kv_load_failure_policy: recompute`. Its `cpu_bytes_to_use` was 100 GiB per
local DP engine, or up to 800 GiB on the DP=8 prefill pod.

## Workload and measurement window

The AIPerf workload used `semianalysis_cc_traces_weka_062126`, concurrency 32,
48 dataset entries, maximum synthesized ISL 115,000, maximum synthesized OSL
2,048, and random seed 67. Requests were admitted for 300 seconds with a
120-second grace period.

The summary uses the terminal-by-cutoff method: a request counts only when its
terminal timestamp is no later than 300 seconds after the first credit was
issued. Requests admitted by the cutoff but completed later are retained in
the raw artifacts and excluded from the table. This makes the comparison
independent of post-window drain time. Because this is a closed-loop workload,
the faster arm can admit more requests within the same window.

## Results

| Arm | Terminal | OK/error | Success req/s | Input ktok/s | Output tok/s | Admitted but not terminal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Precise PD NIXL | 408 | 404/4 | 1.347 | 64.453 | 576.553 | 22 |
| Precise PD Multi Tier | 437 | 430/7 | 1.433 | 68.467 | 638.157 | 19 |
| Multi Tier delta | +7.1% | +26 OK | +6.4% | +6.2% | +10.7% | -3 |

| Arm | Mean TTFT | P50 TTFT | P90 TTFT | P99 TTFT | Mean latency | P50 latency | P90 latency | P99 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Precise PD NIXL | 10.618 s | 7.754 s | 25.040 s | 45.411 s | 17.411 s | 14.031 s | 35.321 s | 61.576 s |
| Precise PD Multi Tier | 9.174 s | 5.468 s | 22.339 s | 42.726 s | 16.038 s | 12.244 s | 33.928 s | 58.236 s |
| Multi Tier delta | -13.6% | -29.5% | -10.8% | -5.9% | -7.9% | -12.7% | -3.9% | -5.4% |

All cutoff errors were HTTP 400 context-length rejections after the same Weka
conversation reached 120,000 input tokens. Multi Tier has three more such
errors because it progressed to three later turns before the cutoff. Both arms
recorded zero failed NIXL transfers, failed NIXL notifications, and expired
NIXL requests.

## Mechanism evidence

The NIXL arm exposed no CPU-offload counters. AIPerf's run-level counter
deltas, including the post-cutoff drain, recorded:

- 344.82 GiB transferred from GPU to CPU.
- 25.23 GiB restored from CPU to GPU.
- Non-zero CPU-to-GPU bytes on seven of eight DP ranks.
- An aggregate prefill prefix-hit ratio of 66.73%, versus 63.24% for NIXL.

The non-zero restore bytes distinguish this result from a run that merely
allocates a CPU tier or writes unused copies. These restores are local CPU
reads.

## Conclusion

In this single Weka C32 cut, CPU Multi Tier improved successful request rate
by 6.4%, reduced mean TTFT by 13.6%, and reduced mean request latency by 7.9%.
The mechanism counters and higher prefix-hit ratio are consistent with CPU
retention avoiding some repeated prefill work.

This is a directional result, not a stable capacity estimate. Repeat both
arms and reverse their order before publishing the percentages as expected
performance. The topology also differs from a 2-prefill, 2-decode C64 test:
it uses half the pods and half the concurrency while preserving four
concurrent requests per prefill DP rank.
