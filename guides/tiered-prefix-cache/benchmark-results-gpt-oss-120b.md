## High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

The benchmark runs on 16 × H100 GPUs, distributed across 16 model servers (1 H100s per server with TP=2) using gpt-oss-120B and the same workload as in [default configuration benchmark results](#README.md/benchmarking-results). The benchmark compares to optimized baseline configuration.

### Throughput

| Metric | llm-d baseline | KV cache offload | Delta |
| --- | --- | --- | --- |
| Requests/sec (successful) | 9.69 | 11.48 | **+1.8 (+18.4%)** |
| Output tokens/sec | 3,704 | 4,120 | **+416.0 (+11.2%)** |
| Total tokens/sec | 166,056 | 196,332 | **+30276 (+18.2%)** |
| Input tokens/sec | 162,353 | 192,212 | **+29859 (+18.4%)** |

### Latency (successful requests only)

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

### vLLM Server Metrics (fleet aggregate)

| Metric | llm-d baseline | KV cache offload |
| --- | --- | --- |
| **Internal GPU cache hit rate** | **1.1%** | **5.3%** |
| Internal cache hits (tokens) | 27.0M | 45.6M |
| Internal cache queries (tokens) | 2564.7M | 853.6M |
| **External (CPU offload) hit rate** | N/A | **45.1%** |
| External cache hits (tokens) | N/A | 61.0M |
| External cache queries (tokens) | N/A | 135.3M |


### Previous Benchmarking Results

> [!NOTE]
> The following benchmark results were from a previous release and does not match the deployment of the current release. A follow up benchmark will be conducted and the results will be updated accordingly. See <https://github.com/llm-d/llm-d/issues/680>.

### GPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 9.0 | 20.9 | 37.8 | 49.7 | 38,534.8 |
| **vLLM + CPU offloading 100GB** | 6.7 (-25.6%) | 20.2 (-3.3%) | 30.9 (-18.3%) | 44.2 (-11.1%) | 46,751.0 (+21.3%) |
| **vLLM + LMCache CPU offloading 100GB** | 6.5 (-27.8%) | 18.8 (-10.0%) | 30.8 (-18.5%) | 43.0 (-13.5%) | 46,910.6 (+21.7%) |

#### Low Cache Scenario (KVCache < HBM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.12 | 0.09 | 18.4 | 19.6 | 23,389.6 |
| **vLLM + CPU offloading 100GB** | 0.13 | 0.11 | 18.6 | 20.6 | 23,032.6 |
| **vLLM + LMCache CPU offloading 100GB** | 0.15 | 0.10 | 18.9 | 19.6 | 22,772.5 |

### TPU

#### High Cache Scenario (HBM < KVCache < HBM + CPU RAM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.98 | 2.1 | 22.1 | 26.2 | 67262.3 |
| **vLLM + CPU offloading 25000 Chunks** | 0.56 (-49%) | 0.5 (-75.7%) | 20.3 (-8.1%) | 23.6 (-9.9%) | 73178.1 (+8.9%) |

#### Low Cache Scenario (KVCache < HBM)

| Medium Configuration | Mean TTFT (second) | P90 TTFT (second) | Mean E2E Latency (second) | P90 E2E Latency (second) | Overall Throughput (token per second) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM** | 0.24 | 0.23 | 16.9 | 19.9 | 25715.9 |
| **vLLM + CPU offloading 25000 Chunks** | 0.26 | 0.24 | 17.4 | 20.2 | 23,032.6 |
