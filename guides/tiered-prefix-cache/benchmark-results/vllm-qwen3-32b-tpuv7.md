# Qwen/Qwen3-32B CPU Offloading Benchmark (Google TPU v7)

Headline throughput and latency comparison showing the impact of prefix-cache offloading on Google TPU v7.

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Overall Throughput (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HBM-only | 0.98 | 2.1 | 22.1 | 26.2 | 67262.3 |
| HBM + CPU RAM (25000 Chunks) | 0.56 (-49%) | 0.5 (-75.7%) | 20.3 (-8.1%) | 23.6 (-9.9%) | 73178.1 (+8.9%) |
