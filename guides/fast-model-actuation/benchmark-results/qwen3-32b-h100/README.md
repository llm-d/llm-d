# EPP+KEDA+FMA Benchmark Report

Three actuation paths, measured under KEDA [saturation-based][guide] and
[queue-based][queue-guide] autoscaling. Both sections compare the same passes,
which differ only in **who owns the model-server lifecycle**:

| Pass | Model load |
|---|---|
| **Baseline** | Cold — a new decode pod loads Qwen3-32B on every scale-up |
| **Warm** | FMA launcher already running; scale-up creates a new vLLM instance |
| **Hot** | All replicas pre-loaded during standup, then scaled to 1 (vLLM sleeps); scale-up wakes a sleeping instance |

Run on an NVIDIA H100-80GB-HBM3 OpenShift cluster, one GPU per replica. Every
metric reported below is defined in [Metric Definitions](#metric-definitions).

> [!NOTE]
> Figures below are from a **single run per pass**, not an average across
> repeats, so small differences between columns should not be read as
> significant. Within each section all three passes used identical workload,
> model, and autoscaling configuration — only the model-server lifecycle owner
> differs.

## Saturation-Based Autoscaling

These three passes use the autoscaling setup from
[keda-epp-saturation][guide] — KEDA polling the EPP
`llm_d_epp_flow_control_pool_saturation` and `llm_d_epp_request_running` gauges,
EPP `flowControl` feature gate on, the optimized-baseline scheduler plugins, and
a KEDA-generated HPA on the scale target.

[guide]: ../../../workload-autoscaling/keda-epp-saturation/README.md

### Configuration

The [keda-epp-saturation][guide] configuration, with `maxReplicaCount` lowered
to 6 and HPA scale-up/scale-down policies added. Thresholds, polling interval,
trigger queries, metric source, and plugin set are the guide's defaults.

| Component | Parameter | Value |
|---|---|---|
| Model | Name / max length | `Qwen/Qwen3-32B` / 16000 |
| Model | GPU memory utilization | 0.95 |
| Model | Tensor parallelism | 1 (1 GPU per replica) |
| KEDA | Trigger 1 — pool saturation threshold | `0.7` |
| KEDA | Trigger 2 — running requests threshold | `16` |
| KEDA | Polling interval | 15 s |
| HPA | Min / Max replicas | 1 / 6 |
| HPA | Scale-up | 0 s stabilization, 1 Pod / 180 s |
| HPA | Scale-down | 300 s stabilization, 1 Pod / 300 s |
| Workload | Harness / profile | `inference-perf` / `shared_prefix_synthetic_heavy.yaml` |
| Workload | Load pattern | constant, 2 workers — 1 RPS aggregate for 120 s, then 4 RPS for 300 s |
| Workload | Prompt shape | 4000-token shared prefix + 256-token question, 256-token output |
| Idle replicas | Baseline / Warm / Hot | 1 decode pod / 1 requester / 6 requesters pre-loaded, then scaled to 1 |

### Results

| Metric | Baseline | Warm | Hot | Δ% Hot vs Baseline |
| :------------------------------------- | -------: | -------: | -------: | -----: |
| Duration (s)                           | 435      | 436      | 437      | +0.5%  |
| Total requests                         | 1,320    | 1,320    | 1,320    | —      |
| P99 TTFT (ms)                          | 102,517.5 | 87,245.2 | 24,263.6 | −76.3% |
| P99 ITL (ms/tok)                       | 430.6    | 432.8    | 437.8    | +1.7%  |
| Avg replicas                           | 1.86     | 3.17     | 2.34     | +25.8% |
| Max replicas                           | 6        | 6        | 3        | −50.0% |
| Avg KV cache utilization               | 60.9%    | 46.9%    | 52.8%    | −13.3% |
| Avg queue depth (EPP)                  | 19.9     | 5.1      | 1.1      | −94.5% |
| Avg flow-control pool saturation (EPP) | 3.93     | 1.75     | 0.76     | −80.7% |
| Avg flow-control queue (EPP)           | 30.1     | 13.6     | 1.0      | −96.7% |
| Avg running requests (EPP)             | 38.5     | 32.7     | 19.3     | −49.9% |
| Avg pod startup (s)                    | 183      | 102      | 4        | −97.8% |
| Hot hit rate                           | _n/a_    | 0.0%     | 100.0%   | —      |
| Warm hit rate                          | _n/a_    | 100.0%   | 0.0%     | —      |
| Failures                               | 0        | 0        | 0        | —      |

Pod startup is the mechanism behind the rest: 183 s (baseline, cold model load)
→ 102 s (warm, new vLLM on a live launcher) → 4 s (hot, wake a sleeping vLLM).
Because scale-up relief arrives ~45× faster in the hot path, queues never build
and **P99 TTFT falls 76%** — from 102.5 s to 24.3 s. The cost is ~26% more average
replicas: the hot path scales out sooner precisely because it can.

P99 ITL is flat across all three (~431-438 ms/tok), which is expected: once a
request is being decoded it streams at the same rate regardless of how its
replica was actuated. The actuation path affects how long a request *waits*,
not how fast it generates.

The `n/a` hit rates for Baseline are expected: there is no FMA dual-pods
controller in that pass, so no actuations to classify. The 100% split between
Warm and Hot confirms each pass exercised its intended path — every warm
actuation was a `create_instance`, every hot actuation a `wake`.

## Queue-Based Autoscaling

The same three passes under the [keda-epp-queue][queue-guide] setup, which
scales on EPP queue depth (`llm_d_epp_flow_control_queue_size`) rather than pool
saturation, with running requests as the second trigger.

[queue-guide]: ../../../workload-autoscaling/keda-epp-queue/README.md

### Configuration

The [keda-epp-queue][queue-guide] configuration, with `maxReplicaCount` lowered
to 6. Thresholds, polling interval, trigger queries, metric source, and plugin
set are the guide's defaults. Model and workload are unchanged from the
saturation section.

| Component | Parameter | Value |
|---|---|---|
| KEDA | Trigger 1 — queue size threshold | `1` |
| KEDA | Trigger 2 — running requests threshold | `16` |
| KEDA | Polling interval | 15 s |
| HPA | Min / Max replicas | 1 / 6 |
| HPA | Scale-up | 0 s stabilization, 100% / 15 s |
| HPA | Scale-down | 300 s stabilization, 100% / 15 s |

### Results

| Metric | Baseline | Warm | Hot | Δ% Hot vs Baseline |
| :------------------------------------- | -------: | -------: | -------: | -----: |
| Duration (s)                           | 450      | 435      | 434      | −3.6%  |
| Total requests                         | 1,320    | 1,320    | 1,320    | —      |
| P99 TTFT (ms)                          | 142,686.9 | 119,549.8 | 25,889.3 | −81.9% |
| P99 ITL (ms/tok)                       | 430.7    | 434.2    | 415.8    | −3.5%  |
| Avg replicas                           | 2.53     | 3.00     | 3.93     | +55.3% |
| Max replicas                           | 6        | 6        | 6        | —      |
| Avg KV cache utilization               | 49.8%    | 48.9%    | 31.2%    | −37.3% |
| Avg queue depth (EPP)                  | 19.9     | 10.5     | 0.3      | −98.5% |
| Avg flow-control pool saturation (EPP) | 3.20     | 2.34     | 0.51     | −84.1% |
| Avg flow-control queue (EPP)           | 41.1     | 25.1     | 0.2      | −99.5% |
| Avg running requests (EPP)             | 48.8     | 36.0     | 13.7     | −71.9% |
| Avg pod startup (s)                    | 158      | 96       | 7        | −95.6% |
| Hot hit rate                           | _n/a_    | 0.0%     | 100.0%   | —      |
| Warm hit rate                          | _n/a_    | 100.0%   | 0.0%     | —      |
| Failures                               | 0        | 0        | 0        | —      |

The same mechanism holds, more sharply: pod startup 158 s → 96 s → 7 s, and
**P99 TTFT falls 82%** (142.7 s → 25.9 s). Because a queue-size threshold of `1`
scales out on the first sign of backlog, the hot path drives the flow-control
queue almost to zero (41.1 → 0.2) and holds average queue depth at 0.3.

That responsiveness costs more capacity than the saturation triggers did: **~55%
more average replicas** (2.53 → 3.93, versus ~26% under saturation), and KV cache
utilization drops to 31.2% as load spreads across more replicas. Queue-based
scaling buys lower latency by running warmer.

## Metric Definitions

Shared by both sections above. Which EPP gauge acts as the KEDA scale trigger
differs — `llm_d_epp_flow_control_pool_saturation` for saturation-based,
`llm_d_epp_flow_control_queue_size` for queue-based, with
`llm_d_epp_request_running` as the second trigger in both — but all gauges are
reported throughout so the two modes can be compared on the same axes.

| Metric | Definition |
|---|---|
| Duration | Wall-clock length of the benchmark window (s) |
| Total requests | Requests issued by the harness over the run |
| P99 TTFT | 99th-percentile time-to-first-token (ms) — lower is better |
| P99 ITL | 99th-percentile inter-token latency (ms/token) — lower is better |
| Avg replicas | Mean ready pod count during the test window |
| Max replicas | Peak ready pod count; 6 means the `maxReplicaCount` ceiling was reached |
| Avg KV cache utilization | Mean GPU KV cache utilization across serving replicas |
| Avg queue depth (EPP) | Mean pending-request queue depth at the endpoint proxy |
| Avg flow-control pool saturation (EPP) | Mean `llm_d_epp_flow_control_pool_saturation`; >1.0 means the pool is overloaded and throttling |
| Avg flow-control queue (EPP) | Mean EPP flow-control queue size |
| Avg running requests (EPP) | Mean `llm_d_epp_request_running` — in-flight requests across the pool |
| Avg pod startup | Mean time for a new replica to become ready and serve (s) — lower is better |
| Hot hit rate | Share of run-phase FMA actuations that woke a sleeping vLLM (`wake`) |
| Warm hit rate | Share of run-phase FMA actuations that created a new vLLM on an existing launcher (`create_instance`) |
| Failures | Requests that did not complete successfully |
| Δ% Hot vs Baseline | Relative change from the Baseline column to the Hot column; sign follows the raw value, so a negative delta is an improvement for the latency, queue, and startup metrics |
