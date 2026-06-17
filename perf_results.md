# EPP Router Performance Benchmarking Results

| Timestamp | Namespace | EPP Images | Container | Idle CPU (m) | Idle Mem (MiB) | Peak CPU (m) | Peak Mem (MiB) | P50 Latency (ms) | P95 Latency (ms) | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-17 20:25:59 | llm-d-perf-1781727959 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | TOTAL | 137 | 33 | 4355 | 123 | 0.05 | 0.10 | SUCCESS |
| 2026-06-17 20:25:59 | llm-d-perf-1781727959 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | envoy-proxy | 14 | 13 | 2124 | 31 | 0.05 | 0.10 | SUCCESS |
| 2026-06-17 20:25:59 | llm-d-perf-1781727959 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | epp | 123 | 20 | 2231 | 95 | 0.05 | 0.10 | SUCCESS |
| 2026-06-17 21:17:54 | llm-d-perf-1781731074 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | TOTAL | 240 | 35 | 4107 | 128 | 0.05 | 0.10 | SUCCESS |
| 2026-06-17 21:17:54 | llm-d-perf-1781731074 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | envoy-proxy | 13 | 13 | 1963 | 30 | 0.05 | 0.10 | SUCCESS |
| 2026-06-17 21:17:54 | llm-d-perf-1781731074 | docker.io/envoyproxy/envoy:distroless-v1.33.2<br>ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main | epp | 227 | 22 | 2154 | 98 | 0.05 | 0.10 | SUCCESS |
