# Benchmark Results: TPU7x Dynamic Sub-Slicing (2x2x1)

Provisioning-path benchmarks for llm-d model servers on GKE TPU7x dynamic sub-slices. Serving-engine throughput and latency are not reported separately: the vLLM configuration is identical to the corresponding static-topology TPU recipe, so engine performance matches the [Optimized Baseline benchmark results](../../../../benchmark-results/); dynamic slicing changes only how slices are provisioned, scheduled, and recovered.

## Setup

* **Workload**: `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`, aggregated vLLM, TP=8, one `2x2x1` sub-slice (4 chips, 1 host) per LWS replica.
* **Cluster**: GKE Standard (Rapid channel), `tpu7x-standard-4t` node pools provisioned as `4x4x4` sub-blocks in `provision_only` mode, Kueue TAS with the GKE Kueue slice controller.
* **Measurement dates**: 2026-05-22 (node-failure MTTR), 2026-06-10 (scale-up latency). Measured during the dynamic-slicing beta on a vLLM TPU nightly build; absolute numbers may shift on the GA stack.

## Recovery After Node Failure (MTTR)

Five node failures were induced on hosts backing active `2x2x1` sub-slices. MTTR is measured from the failure timestamp until the replacement pod reaches `PodReadyToStartContainers` on a re-formed sub-slice; it excludes container image pull, model weight loading, and engine warmup, which are identical to the static-topology path.

| Replica | Failure -> quota reserved | -> Slice CR created | -> slice ready | -> workload admitted | -> pod ready to start | MTTR (s) |
| --- | --- | --- | --- | --- | --- | --- |
| intl-30 | 12s | 31s | 54s | 55s | 56s | 56 |
| intl-65 | 12s | 18s | 39s | 40s | 42s | 42 |
| intl-66 | 11s | 29s | 52s | 53s | 55s | 55 |
| intl-54 | 10s | 30s | 53s | 54s | 55s | 55 |
| intl-78 | 11s | 27s | 55s | 56s | 58s | 58 |

**MTTR: 42-58s (mean 53s).** With static node pools, recovery from a comparable failure requires node repair or replacement before the pod can reschedule, which takes minutes; the slice controller instead re-forms the sub-slice on healthy spare partitions of the same pre-provisioned sub-blocks.

## Workload Scale-Up Latency vs. Slice Count

Concurrent scale-up of N `2x2x1` sub-slice replicas from zero. Latency is per replica, from scale-up request until the pod reaches `PodReadyToStartContainers` (quota reservation, Slice CR creation, ICI activation, Kueue admission, and scheduling included; image pull and model load excluded).

| Concurrent slices | p50 (s) | p90 (s) | p95 (s) | p99 (s) |
| --- | --- | --- | --- | --- |
| 16 | 30 | 35 | 36.3 | 39.3 |
| 32 | 34 | 43 | 45.3 | 48 |
| 64 | 36 | 42 | 43 | 45.7 |
| 128 | 41 | 47 | 48 | 51 |
| 256 | 41 | 49 | 50 | 51.8 |

Slice formation latency grows sub-linearly with concurrency: scaling from 16 to 256 concurrent slice creations increases p50 by 11s and p99 by 12.5s.
