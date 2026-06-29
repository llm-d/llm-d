# Qwen/Qwen3-32B SGLang CPU Offloading Benchmark (16×H100)

The benchmark runs on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2) using Qwen3-32B and SGLang v0.5.13.post1.

All results show the effect of enabling SGLang HiCache prefix-cache offloading (`--hicache-size 200`) relative to an HBM-only configuration, under a high-cache scenario where the working set exceeds HBM but fits within HBM + CPU RAM.

* **Workload**: 250 prefix groups, 5 prompts per group, system prompt length of 16,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **GPU Cache Size (Total)**: 321,856 tokens per replica (includes ~78.6 GiB of the 160 GiB of total GPU memory across 2 H100 GPUs per replica).
* **CPU Cache Size (Total)**: 819,200 tokens per replica (200 GiB / replica host RAM).
* **Workload Unique Cache (Working Set)**: 4,640,000 tokens (~760 GB / 708 GiB).

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **5.0 QPS** | HBM-only | 2.12 | 4.35 | 9.54 | 13.67 | 68,517.5 |
| | HBM + CPU RAM | 0.95 (-55.2%) | 1.39 (-68.0%) | 10.26 (+7.5%) | 18.55 (+35.7%) | 73,860.3 (+7.8%) |
| **10.0 QPS** | HBM-only | 9.76 | 19.66 | 25.62 | 34.78 | 111,155.9 |
| | HBM + CPU RAM | 0.43 (-95.6%) | 1.27 (-93.5%) | 7.74 (-69.8%) | 11.08 (-68.1%) | 152,045.3 (+36.8%) |
| **20.0 QPS** | HBM-only | 40.55 | 76.70 | 53.77 | 87.84 | 117,451.7 |
| | HBM + CPU RAM | 1.56 (-96.2%) | 3.99 (-94.8%) | 9.61 (-82.1%) | 12.21 (-86.1%) | 280,447.9 (+138.8%) |
| **40.0 QPS** | HBM-only | 107.88 | 185.77 | 121.07 | 199.02 | 115,459.0 |
| | HBM + CPU RAM | 25.56 (-76.3%) | 47.25 (-74.6%) | 33.61 (-72.2%) | 54.66 (-72.5%) | 308,779.8 (+167.4%) |
