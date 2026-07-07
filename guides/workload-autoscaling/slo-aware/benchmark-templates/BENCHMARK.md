# Benchmarking the KEDA predicted-latency autoscaler

How we measure the setup in [../../README.slo-aware.md](../../README.slo-aware.md): a staged-ramp
episode scored on **combined SLO attainment** (fraction of requests meeting
*both* latency SLOs) against **cost** (time-averaged replicas over the full
episode, including the final drain).

This folder is the reusable harness — `scored-config.yml` (the graded load
profile), `ip-burnin-config.yml` (the unscored predictor warm-up), and
`plot_run.py` (fetch series → reconstruct the desired line → plot + score).
Our run's output lands in `../benchmark-results/`.

`plot_run.py` needs matplotlib (`pip install -r requirements.txt`) and a
`kubectl` on your PATH pointed at the benchmark cluster — it fetches the series
by `kubectl exec`-ing into the Prometheus pod.

## Environment

| | |
|---|---|
| Cluster | GKE, NVIDIA H100 80 GB nodes |
| Model | Qwen/Qwen3-32B, vLLM, TP=2 (2 GPUs per replica) |
| Gateway | llm-d EPP per the [optimized-baseline guide](https://github.com/llm-d/llm-d/tree/main/guides/optimized-baseline) + `predicted-latency-producer` (streaming mode) |
| SLOs | TTFT ≤ 3000 ms, TPOT ≤ 100 ms |
| Bounds | 3–8 replicas |
| Prometheus | EPP scraped at 5 s; recording rules evaluated at 15 s |

Pod warmup is engineered down to ~100 s (from ~2 m 15 s) with a
torch-compile cache on a hostPath volume plus a fast startupProbe (5 s
period); without it, every scale-up pays an extra ~75 s of violations.

## Workload

[inference-perf](https://github.com/kubernetes-sigs/inference-perf) running
a Prefill-Heavy profile: ~4000 input / ~1000 output tokens per request,
random data, streaming completions, staged constant-rate ramp:

```
2 rps (330 s) → 4 (360) → 6 (360) → 8 (360) → 10 (360) → 4 (360) → 1 (300)
```

Requests carry `x-llm-d-slo-ttft-ms: 3000` / `x-llm-d-slo-tpot-ms: 100`
headers. These are **not** needed for scaling — they make the EPP emit
per-request SLO-violation counters, which is how attainment is scored
server-side. (Scoring client-side from the inference-perf report works too.)

## Protocol

1. **Settle**: pool drained to the 3-replica floor, signal reading 0.
2. **Burn-in (unscored)**: the EPP's latency predictor trains *online* and
   its calibration resets on every EPP restart. After any restart, run one
   throwaway pass across the load range (we use 2→6→9→3 rps, ~19 min).
   Skipping this costs real accuracy: on the identical configuration we
   measured 85.6 % attainment with a cold predictor vs 95.8 % calibrated.
3. **Scored episode**: launch the profile above as a Job; record the start
   timestamp. The episode window runs from job start until the pool has
   drained back to the floor after the load ends.
4. **Score**:
   - *Attainment* — from the EPP violation counters over the exact window:
     `1 − (increase(ttft_violations) + increase(tpot_violations)) / increase(requests)`
     computed per-request (a request must meet both SLOs to count).
     Cross-check the shape against the inference-perf report percentiles.
   - *Cost* — `avg_over_time(kube_deployment_status_replicas[<window>])`,
     × 2 H100s per replica.
5. Watch the loop live (optional): the signal, formula output, and replica
   counts tell the whole story — floor-hold at low rate, clamp + burst
   scale-up at the knee, hysteresis hold near the boundary, 1-pod drain
   steps after the load falls.

## Results

This KEDA loop, averaged over repeated scored episodes:

| Configuration | Combined SLO attainment | Avg replicas (full episode) |
|---|---|---|
| Static pool sized for peak (8) | 100 % | 8 |
| **KEDA loop (this guide)** | **95.8 %** | **5.93** |

A single episode is plotted in
[`../benchmark-results/scored-run-overview.png`](../benchmark-results/scored-run-overview.png)
(offered load, TTFT/TPOT p90 vs SLO, replicas). `plot_run.py` also emits a
raw-vs-smoothed control-signal panel when you re-run it.

Individual episodes vary with predictor calibration. When residual violations
appear they are almost entirely TTFT, concentrated in a single transient at the
first capacity crossing — the pool held at the floor while healthy, then a load
step landed before capacity could arrive, and TTFT spiked until the new pods
finished their ~100 s warmup. This is the structural violation bill,
(signal lag + ask lag + grant lag + warmup) × arrival rate; the peak stages run
clean because capacity is already in place. The plotted episode caught one such
crossing.
