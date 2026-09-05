# Qwen/Qwen3-Coder-480B-A35B-Instruct CPU Offloading Benchmark (24×TPU v7)

The benchmark runs on 24 × TPU v7 chips, distributed across 3 model servers (multi-host) (8 TPU chips per server with TP=16) using Qwen/Qwen3-Coder-480B-A35B-Instruct.

All results show the effect of enabling prefix-cache offloading relative to an HBM-only configuration, under a high-cache scenario where the working set exceeds HBM + CPU RAM.

* **Workload**: 240 prefix groups, 40 prompts per group, system prompt length of 16,000 tokens, question length of 256 tokens, output length of 1024 tokens.

| Configuration | Mean TTFT (s) | Mean ITL (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Overall Throughput (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| HBM-only | 0.91 | 0.027 | 32.21 | 34.36 | 16140.64 |
| HBM + CPU RAM (10000 Chunks) | 0.44 (-51.7%) | 0.024 (-11.1%) | 28.56 (-11.3%) | 30.22 (-12%) | 18217.53 (+12.8%) |
