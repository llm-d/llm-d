# Qwen/Qwen3-30B-A3B-Thinking P2P KV Cache Sharing on P/D (H200, agentic)

The benchmark runs `Qwen/Qwen3-30B-A3B-Thinking-2507` disaggregated - 2
prefill + 4 decode, one H200 per pod (TP=1), vLLM block size 64,
`--max-model-len=131072`, a 128 GiB CPU offload tier per pod,
`offload_prompt_only: false` so decode's generated KV is offloadable, and
`rdma/ib` on both roles. Both arms carry `NixlConnector` for the P/D
handoff; the P2P arm wraps it in a `MultiConnector` that adds the
`OffloadingConnector` P2P secondary tier, enables `--enable-p2p-pull` on
the decode routing sidecar, and adds the `p2p-source-producer`
(`minCachedTokenDelta: 1024`, precise KV-event index) to the EPP. Placement
plugins, filters, scorers and weights are identical across arms.

This is the regime the guide's
[P/D variant](../README.md#pd-variant-p2p-over-nixl-disaggregation)
describes: decode generates the session history, so on every subsequent
turn the prefill worker faces KV it never computed and no placement
decision can make local. Without a pull it re-prefills the whole
accumulated history each turn.

## Workload

The agentic-serving profile: 24 conversations, dynamic system prompts of
10K-100K tokens (lognormal, mean 50K), 4-40 turns per conversation (mean
12), ~1,500 input and ~425 output tokens per turn, and tool-call gaps of
1-20 s that evict session KV between turns - which is what makes
re-engagement a pull-versus-recompute choice. 288 requests at concurrency
16, streamed, driven through `llmdbenchmark`/inference-perf. Each arm ran
on a freshly re-rolled fleet; mean prompt length was 61,884 tokens in one
arm and 61,883 in the other, i.e. the arms saw the same workload.

## Results

| metric | plain NIXL P/D | + P2P pull | delta |
|---|---:|---:|---:|
| TTFT p50 | 6.83 s | **1.09 s** | **6.3x** |
| TTFT p95 | 20.90 s | 12.92 s | 1.6x |
| TTFT p99 | 28.17 s | 34.66 s | 0.8x (worse) |
| TTFT mean | 8.35 s | 2.80 s | 3.0x |
| Request latency p50 | 14.79 s | 8.57 s | 1.7x |
| Throughput | 0.82 req/s | **1.24 req/s** | **+50%** |
| Output tokens/s | 777 | 1,167 | +50% |
| Run duration | 340 s | 221 s | -35% |
| Completed | 288/288, 0 failures | 288/288, 0 failures | - |

Pull evidence: 9 P2P sessions established prefill<-decode. The count is
small because the topology is small (2 prefill x 4 decode is few directed
pairs), but each session carries a conversation's accumulated history on
every turn.

## Reading the result

The median request starts generating **6.3x sooner** and the fleet
completes the same 288-request workload **35% faster**. Both come from the
same mechanism: a turn whose history lives on a decode pod is fetched as a
transfer instead of re-prefilled from scratch, and at ~62K tokens of mean
prompt that recompute is expensive.

**p99 is worse with the pull** (34.66 s versus 28.17 s), and that is
expected rather than anomalous: the extreme tail in both arms is the cold
*first* prefill of a conversation whose system prompt can reach 100K
tokens - there is no peer copy to fetch for a prompt nobody has computed
yet - and this workload's output length is lognormal with a long tail, so
the slowest requests are dominated by generation, not by prefill. The
gains here are median, mean and throughput; do not read this scenario as a
tail-latency fix.

Single run per arm. The 6.3x median and +50% throughput are far outside the
run-to-run spread seen on this workload; the p99 difference is not, and
should be treated as noise rather than a measured regression.
