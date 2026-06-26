# openai/gpt-oss-120b CPU Offloading Benchmark (16×H100)

The benchmark runs on 16 × H100 GPUs, distributed across 16 model servers (1 H100 per server with TP=1) using gpt-oss-120B and the same workload as the [GPU benchmarking report](./vllm-qwen3-32b-h100.md). The results below show the effect of enabling prefix-cache offloading relative to an HBM-only configuration.

## Throughput

| Metric | llm-d baseline | KV cache offload | Delta |
| --- | --- | --- | --- |
| Requests/sec (successful) | 9.69 | 11.48 | **+1.8 (+18.4%)** |
| Output tokens/sec | 3,704 | 4,120 | **+416.0 (+11.2%)** |
| Total tokens/sec | 166,056 | 196,332 | **+30276 (+18.2%)** |
| Input tokens/sec | 162,353 | 192,212 | **+29859 (+18.4%)** |

## Latency (successful requests only)

| Metric | llm-d baseline | KV cache offload | Delta |
| --- | --- | --- | --- |
| Mean request latency | 19.2s | 5.3s | **-13.9 (-72.3%)** |
| Median request latency | 15.9s | 4.4s | **-11.5 (-72.4%)** |
| P90 request latency | 41.6s | 10.9s | **-30.7 (-73.9%)** |
| Mean TTFT | 12.5s | 1.2s | **-11.2 (-90.0%)** |
| Median TTFT | 7.6s | 0.5s | **-7.0 (-92.9%)** |
| P90 TTFT | 33.8s | 4.5s | **-29.3 (-86.7%)** |
| **Mean TPOT** | 26.1ms | 15.7ms | **-10.4 (-39.8%)** |
| Median TPOT | 30.2ms | 15.2ms | **-15.0 (-49.7%)** |
| P90 TPOT | 36.4ms | 25.4ms | **-11.0 (-30.3%)** |
| ITL mean | 26.1ms | 15.7ms | **-10.4 (-39.8%)** |

## vLLM Server Metrics (fleet aggregate)

| Metric | llm-d baseline | KV cache offload |
| --- | --- | --- |
| **Internal GPU cache hit rate** | **1.1%** | **5.3%** |
| Internal cache hits (tokens) | 27.0M | 45.6M |
| Internal cache queries (tokens) | 2564.7M | 853.6M |
| **External (CPU offload) hit rate** | N/A | **45.1%** |
| External cache hits (tokens) | N/A | 61.0M |
| External cache queries (tokens) | N/A | 135.3M |


## Per-Stage Breakdown (5–40 QPS)

| Target Rate | Configuration | Mean TTFT | P90 TTFT | Mean E2E Latency | P90 E2E Latency | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **5 QPS** | optimized-baseline | 0.45s | 0.57s | 1.80s | 2.05s | 1,983 |
| | cpu-offload | 0.43s (-3.7%) | 0.56s (-2.1%) | 1.80s (-0.1%) | 2.04s (-0.2%) | 1,738 (-12.3%) |
| **10 QPS** | optimized-baseline | 0.44s | 0.56s | 1.98s | 2.38s | 3,664 |
| | cpu-offload | 0.37s (-17.9%) | 0.55s (-1.7%) | 1.91s (-3.3%) | 2.28s (-4.3%) | 3,799 (+3.7%) |
| **15 QPS** | optimized-baseline | 0.45s | 0.57s | 2.73s | 3.45s | 5,653 |
| | cpu-offload | 0.35s (-22.4%) | 0.55s (-2.4%) | 2.17s (-20.4%) | 2.65s (-23.2%) | 5,171 (-8.5%) |
| **20 QPS** | optimized-baseline | 0.73s | 1.04s | 5.59s | 9.72s | 6,581 |
| | cpu-offload | 0.32s (-55.8%) | 0.56s (-46.3%) | 2.60s (-53.5%) | 3.39s (-65.1%) | 6,967 (+5.9%) |
| **25 QPS** | optimized-baseline | 6.16s | 10.71s | 14.29s | 18.62s | 7,229 |
| | cpu-offload | 0.34s (-94.5%) | 0.57s (-94.7%) | 3.46s (-75.8%) | 4.61s (-75.3%) | 8,777 (+21.4%) |
| **30 QPS** | optimized-baseline | 14.07s | 26.17s | 22.51s | 34.04s | 6,822 |
| | cpu-offload | 0.33s (-97.6%) | 0.58s (-97.8%) | 4.24s (-81.2%) | 5.50s (-83.8%) | 9,767 (+43.2%) |
| **35 QPS** | optimized-baseline | 18.44s | 35.16s | 26.52s | 41.82s | 7,178 |
| | cpu-offload | 0.50s (-97.3%) | 0.84s (-97.6%) | 6.16s (-76.8%) | 7.41s (-82.3%) | 11,677 (+62.7%) |
| **40 QPS** | optimized-baseline | 24.78s | 45.42s | 32.78s | 52.03s | 6,722 |
| | cpu-offload | 4.24s (-82.9%) | 8.76s (-80.7%) | 10.33s (-68.5%) | 14.63s (-71.9%) | 11,742 (+74.7%) |
