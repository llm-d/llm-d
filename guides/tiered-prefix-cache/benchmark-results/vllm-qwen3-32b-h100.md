# Qwen/Qwen3-32B CPU Offloading Benchmark (16×H100)

The benchmark runs on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2) using Qwen3-32B.

All results show the effect of enabling prefix-cache offloading relative to an HBM-only configuration, under a high-cache scenario where the working set exceeds HBM but fits within HBM + CPU RAM. The weight configuration defaults to `1:1:1:1:1` (Queue Scorer : KV Cache Utilization Scorer : GPU Prefix Cache Scorer : CPU Prefix Cache Scorer : LRU Scorer).

* **Workload**: 250 prefix groups, 5 prompts per group, system prompt length of 16,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **GPU Cache Size (Total)**: 2,384,000 tokens (381 GB / 355 GiB).
* **CPU Cache Size (Total)**: 10,496,000 tokens (~1,718 GB / 1,600 GiB) at 200 GiB/replica.
* **Workload Unique Cache (Working Set)**: 4,640,000 tokens (~760 GB / 708 GiB).

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **5.0 QPS** | HBM-only | 1.62 | 2.65 | 11.98 | 21.60 | 74,638.5 |
| | HBM + CPU RAM | 1.17 (-27.8%) | 1.79 (-32.5%) | 11.49 (-4.1%) | 20.25 (-6.3%) | 82,880.0 (+11.0%) |
| **10.0 QPS** | HBM-only | 8.08 | 16.61 | 26.28 | 32.60 | 122,387.7 |
| | HBM + CPU RAM | 0.41 (-94.9%) | 1.31 (-92.1%) | 6.97 (-73.5%) | 9.23 (-71.7%) | 167,027.5 (+36.5%) |
| **20.0 QPS** | HBM-only | 43.67 | 78.13 | 62.29 | 92.14 | 114,663.2 |
| | HBM + CPU RAM | 0.89 (-98.0%) | 2.66 (-96.6%) | 9.03 (-85.5%) | 11.22 (-87.8%) | 300,749.2 (+162.3%) |
| **40.0 QPS** | HBM-only | 115.57 | 206.39 | 134.78 | 223.93 | 115,645.0 |
| | HBM + CPU RAM | 25.38 (-78.0%) | 48.14 (-76.7%) | 33.95 (-74.8%) | 56.29 (-74.9%) | 331,212.6 (+186.4%) |
