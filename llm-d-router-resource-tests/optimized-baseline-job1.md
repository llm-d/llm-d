# EPP Router Performance Benchmarking Results: optimized-baseline-job1

| Timestamp | Namespace | Guide Name | Perf Job | Machine Family | Sim Replicas | EPP Images | Container | Idle CPU (m) | Idle Mem (MiB) | Peak CPU (m) | Peak Mem (MiB) | P50 Latency (ms) | P95 Latency (ms) | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-18 21:21:29 | llm-d-perf-1781817689 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | TOTAL | 31 | 28 | 92 | 52 | 0.00 | 0.00 | SUCCESS |
| 2026-06-18 21:21:29 | llm-d-perf-1781817689 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | envoy-proxy | 8 | 13 | 30 | 20 | 0.00 | 0.00 | SUCCESS |
| 2026-06-18 21:21:29 | llm-d-perf-1781817689 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | epp | 23 | 15 | 62 | 33 | 0.00 | 0.00 | SUCCESS |
| 2026-06-18 21:37:02 | llm-d-perf-1781818622 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | TOTAL | 198 | 34 | 4495 | 126 | 0.05 | 0.10 | SUCCESS |
| 2026-06-18 21:37:02 | llm-d-perf-1781818622 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | envoy-proxy | 13 | 13 | 2213 | 31 | 0.05 | 0.10 | SUCCESS |
| 2026-06-18 21:37:02 | llm-d-perf-1781818622 | optimized-baseline | shared_prefix_job1.yaml | - | 10 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | epp | 185 | 21 | 2282 | 99 | 0.05 | 0.10 | SUCCESS |
