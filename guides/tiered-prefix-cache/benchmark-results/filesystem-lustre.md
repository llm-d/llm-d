# Lustre Filesystem Offloading Benchmark

> [!NOTE]
> The following benchmark results were from a previous release and do not match the deployment of the current release. A follow up benchmark will be conducted and the results will be updated accordingly. See <https://github.com/llm-d/llm-d/issues/680>.

## LMCache Connector + Lustre

LMCache configuration: `LMCACHE_MAX_LOCAL_CPU_SIZE=20GB`, `LMCACHE_MAX_LOCAL_DISK_SIZE=1120Gi` per GPU (16 GPUs × 1120Gi ≤ 18000Gi Lustre PVC).

### 50K system prompt length (KVCache size 994 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 25.38 | 37.74 | 56.21 | 69.69 | 18607 | 354 | 18962 |
| **vLLM + CPU offloading + Lustre** | 20.12 (-21%) | 34.02 (-9.9%) | 45.83 (-18%) | 58.73 (-16%) | 22827 (+23%) | 435 (+23%) | 23262 (+23%) |

### 70K system prompt length (KVCache size 1.3 TiB) — KV Cache >> (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 58.02 | 74.75 | 87.99 | 105.46 | 16598 | 226.65 | 16825 |
| **vLLM + CPU offloading + Lustre** | 45 (-22%) | 64.79 (-13%) | 68.28 (-22%) | 87.47 (-17%) | 21364 (+28.71%) | 291 (+28.39%) | 21656 (+28.71%) |

## LLM-D FS Connector + Lustre

* CPU RAM allocated: `cpu_bytes_to_use=64424509440` (~64 GB per replica, ~356 GB total for 4 replicas).
* Lustre PVC = 18000 GiB.

### 30K system prompt length (Qwen3-32B, KVCache size 653 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) | ITL (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 2.24 | 5.14 | 22.21 | 26.6 | 27148 | 836 | 27984 | 0.021 |
| **vLLM + CPU offloading + Lustre** | 1.38 (-38.4%) | 2.82 (-45.1%) | 20.45 (-7.9%) | 22.77 (-14.4%) | 28832 (+6.2%) | 828 (-1.0%) | 29661 (+6.0%) | 0.02 (-4.8%) |

### 50K system prompt length (Llama-3.3-70B, KVCache size 994 GiB) — KV Cache > (HBM + CPU RAM)

| Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Input (tok/s) | Output (tok/s) | Overall (tok/s) | ITL (s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline vLLM + CPU offloading** | 27.11 | 41.71 | 57.06 | 72.28 | 18333 | 350 | 18682 | 0.029 |
| **vLLM + CPU offloading + Lustre** | 15.25 (-43.7%) | 24.71 (-40.8%) | 38.55 (-32.4%) | 48.01 (-33.6%) | 27091 (+47.8%) | 517 (+47.7%) | 27609 (+47.8%) | 0.022 (-24.1%) |
