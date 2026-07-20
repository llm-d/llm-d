# Benchmark Report — Speculative Decoding (Qwen3-32B + EAGLE-3)

The benchmark runs **through the llm-d stack** (EPP router + vLLM model server), driven by
`inference-perf`. Two arms on identical hardware and identical router/EPP config — only the
model-server overlay differs — so any delta is attributable to the drafter.

- **Hardware:** 2 × H200 GPU (TP=2, replicas=1)
- **Target / verifier:** `Qwen/Qwen3-32B`
- **Drafter:** `RedHatAI/Qwen3-32B-speculator.eagle3` (`method=eagle3`, `num_speculative_tokens=3`)
- **Arm A (baseline):** `optimized-baseline` overlay, spec decoding off
- **Arm B (spec):** `spec-decoding` overlay, spec decoding on
- **Harness / profile:** `inference-perf` v0.6.0 · `guide_spec-decoding_1.yaml`
- **Load:** 5-stage constant-rate ladder (1 → 2 → 4 → 8 → 16 QPS), 120 s per stage
- **Data:** random tokens, input ~2048 (normal, σ=1024, 10–4096), output ~256 (normal, σ=128, 10–512)

## Headline: the latency win at low QPS

Speculative decoding is a decode-phase optimization; the win shows in **ITL/TPOT**, not TTFT.
At 1 QPS (single-user latency), EAGLE-3 cuts per-token decode time by **27%** and request
latency by **31%**.

| Metric (at 1 QPS)           | Arm A (baseline) | Arm B (spec) | Δ vs baseline |
| :-------------------------- | ---------------: | -----------: | :------------ |
| **TPOT p50 (ms)**           | 13.2             | 9.7          | **−27%**      |
| **ITL mean (ms)**           | 13.3             | 10.3         | **−22%**      |
| Request latency p50 (s)     | 3.60             | 2.50         | **−31%**      |
| Output tokens/s             | 265              | 244          | −8%           |
| TTFT p50 (ms)               | 167              | 160          | −4%           |

> [!NOTE]
> Output throughput is slightly lower at 1 QPS because the system is lightly loaded — per-request
> speed improved but fewer total tokens were in flight. At 2–4 QPS, throughput is equivalent or
> slightly higher with spec decoding.

## Crossover: where the win erodes

As load rises, the drafter's compute overhead competes with real decode work. The benefit erodes
around **4–8 QPS** on this hardware — beyond 8 QPS, the baseline delivers higher throughput and
lower latency.

| QPS | Arm A TPOT p50 (ms) | Arm B TPOT p50 (ms) | Δ TPOT | Arm A tok/s | Arm B tok/s | Δ tok/s |
| --: | ------------------: | ------------------: | -----: | ----------: | ----------: | ------: |
|   1 |                13.2 |                 9.7 |   −27% |         265 |         244 |     −8% |
|   2 |                15.1 |                11.7 |   −22% |         525 |         538 |     +3% |
|   4 |                24.6 |                21.4 |   −13% |       1,006 |       1,032 |     +3% |
|   8 |               209.7 |               210.1 |     0% |       1,649 |       1,442 |    −13% |
|  16 |               222.5 |               220.7 |    −1% |       1,645 |       1,473 |    −10% |

**Crossover QPS: ~4–8** &nbsp;·&nbsp; EAGLE-3 delivers clear per-token wins at ≤4 QPS. At 8 QPS
both arms are saturated and TPOT converges; the baseline pulls ahead on throughput (−13%) because
the drafter consumes GPU resources that could otherwise serve concurrent decodes.

<details>
<summary><b><i>Click</i></b> to view the full per-stage latency breakdown</summary>

| QPS | Arm A ITL mean (ms) | Arm B ITL mean (ms) | Arm A TTFT p50 (s) | Arm B TTFT p50 (s) | Arm A Req Lat p50 (s) | Arm B Req Lat p50 (s) |
| --: | ------------------: | ------------------: | -----------------: | -----------------: | --------------------: | --------------------: |
|   1 |                13.3 |                10.3 |              0.167 |              0.160 |                  3.60 |                  2.50 |
|   2 |                14.1 |                10.3 |              0.166 |              0.191 |                  4.02 |                  3.04 |
|   4 |                23.4 |                21.2 |              0.210 |              0.240 |                  6.29 |                  5.53 |
|   8 |               168.5 |               180.2 |              9.774 |             19.243 |                 55.08 |                 68.83 |
|  16 |               189.8 |               199.9 |             85.730 |            108.233 |                141.83 |                166.64 |

At 8+ QPS, TTFT degrades significantly with spec decoding (19.2 s vs 9.8 s at 8 QPS) — the
drafter's memory footprint reduces available KV cache capacity, causing more prefill queueing.

</details>

## Observations

- **TPOT improvement at low load is the primary win.** At 1–4 QPS, EAGLE-3 reduces TPOT p50 by
  13–27%, translating to proportional request latency reductions.
- **Throughput ceiling is lower.** The baseline peaks at ~1,649 output tok/s; EAGLE-3 peaks at
  ~1,473 tok/s — the drafter's forward passes are overhead at saturation.
- **TTFT regresses under load.** At 8 QPS, TTFT p50 nearly doubles (19.2 s vs 9.8 s). The
  drafter model's memory footprint reduces KV cache headroom, increasing prefill queue depth.
- **At saturation, TPOT converges.** Both arms show ~210–222 ms TPOT p50 at 8–16 QPS — the
  spec decode advantage vanishes when the GPU is compute-bound.

**Recommendation:** enable speculative decoding for latency-sensitive, low-concurrency workloads
(interactive chat, coding assistants) where per-user TPOT matters more than aggregate throughput.
Disable (or scale out instead) above the crossover.
