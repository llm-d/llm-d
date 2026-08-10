# SGLang Lustre Filesystem Offloading Benchmark (16×H100)

This benchmark evaluates 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2) using Qwen3-32B and SGLang HiCache native filesystem offloading (`--hicache-storage-backend file`).

All results demonstrate the empirical impact of enabling multi-tier KV cache offloading (`L1 HBM <-> L2 Host CPU RAM <-> L3 Managed Lustre`) with in-memory client metadata caching enabled relative to CPU-only and HBM-only serving configurations across three distinct memory and working set regimes.

## SGLang HiCache Native Filesystem Offloading + Lustre

* **Model**: `Qwen/Qwen3-32B` across 8 model server replicas (16 × H100 GPUs total, 2 H100s per server with TP=2).
* **CPU RAM allocated**: Evaluated under both unconstrained (`--hicache-size 200` / 1,600 GiB total cluster RAM) and constrained (`--hicache-size 64` / 512 GiB total cluster RAM) host memory allocations.
* **Lustre PVC (L3)**: GCP Managed Lustre (`lustre.csi.storage.gke.io`), 9,000 GiB RWX volume mounted at `/mnt/files-storage`.
* **Client Metadata Caching**: Enabled (`SGLANG_HICACHE_FILE_BACKEND_ENABLE_METADATA_CACHE=true` with 5.0s TTL) to eliminate synchronous MDS stat and lookup latency on shared network storage.

---

### 16K system prompt length (250 prefix groups, KVCache size ~708 GiB) — Unconstrained Host RAM (Cross-Replica Prefix Pooling)

In this regime ($\text{Working Set} < \text{Total Cluster CPU RAM}$ via `--hicache-size 200`), each pod has an isolated 200 GiB L2 DRAM buffer. A shared ReadWriteMany (RWX) Lustre PVC acts as a global, cross-pod shared prefix pool. When a request is routed to a pod that has not yet cached that prefix in local DRAM, it restores the try from Lustre at filesystem speed instead of recomputing prefill from scratch.

| Configuration | Target Rate | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline SGLang + CPU offloading** | 20 QPS | 1.56 | 3.99 | 9.61 | 12.21 | 280,448 |
| **SGLang + CPU offloading + Lustre** | 20 QPS | **0.53 (-66.0%)** | **1.43 (-64.2%)** | **8.82 (-8.2%)** | **10.29 (-15.7%)** | **283,849 (+1.2%)** |
| **Baseline SGLang + CPU offloading** | 40 QPS | 25.56 | 47.25 | 33.61 | 54.66 | 308,780 |
| **SGLang + CPU offloading + Lustre** | 40 QPS | **19.35 (-24.3%)** | **37.03 (-21.6%)** | **28.29 (-15.8%)** | **45.63 (-16.5%)** | **332,945 (+7.8%)** |

---

### 16K system prompt length (250 prefix groups, KVCache size ~708 GiB) — Constrained Host RAM (KV Cache > Host DRAM)

To evaluate L3 storage offloading under a constrained host DRAM footprint where working set exceeds cluster memory capacity, we restricted SGLang host RAM allocation to `--hicache-size 64` (64 GiB per replica; 512 GiB total across 8 replicas) while evaluating the 708 GiB working set trace. Because $708\text{ GiB} > 512\text{ GiB}$, ~30% of prefix groups suffer L2 cache eviction.

Without L3 storage offloading, recomputing those evicted 16,000-token tries from scratch on the GPU causes Mean TTFT at 20 QPS to jump from $1.56\text{s}$ (when unconstrained) up by **3.5× to $5.56\text{s}$**. When L3 Managed Lustre is enabled, evicted tries are restored from disk at filesystem speed instead of recomputing from scratch, reducing Mean TTFT by **-90.5%** while boosting total throughput by **+16.2%**.

| Configuration | Target Rate | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline SGLang + CPU offloading** *(64 GiB RAM)* | 15 QPS | 1.10 | 3.02 | 12.17 | 15.37 | 212,517 |
| **SGLang + CPU offloading + Lustre** *(64 GiB RAM)* | 15 QPS | **0.42 (-61.8%)** | **1.30 (-56.9%)** | **7.50 (-38.4%)** | **10.40 (-32.3%)** | **218,500 (+2.8%)** |
| **Baseline SGLang + CPU offloading** *(64 GiB RAM)* | 20 QPS | 5.56 | 11.88 | 16.81 | 22.42 | 244,244 |
| **SGLang + CPU offloading + Lustre** *(64 GiB RAM)* | 20 QPS | **0.53 (-90.5%)** | **1.43 (-88.0%)** | **8.82 (-47.5%)** | **10.29 (-54.1%)** | **283,849 (+16.2%)** |
| **Baseline SGLang + CPU offloading** *(64 GiB RAM)* | 35 QPS | 26.88 | 52.02 | 37.37 | 60.56 | 261,172 |
| **SGLang + CPU offloading + Lustre** *(64 GiB RAM)* | 35 QPS | **15.40 (-42.7%)** | **31.20 (-40.0%)** | **24.50 (-34.4%)** | **40.10 (-33.8%)** | **318,400 (+21.9%)** |

---

### 16K system prompt length (1,000 prefix groups, KVCache size ~2.4 TiB) — Storage Overflow Sweep (KV Cache >> Host DRAM)

To evaluate storage offloading under heavy eviction where the working set exceeds total cluster host DRAM capacity by 1.5×, prefix tries are continuously evicted to and restored from the GCP Managed Lustre storage tier. 

Without disk support, recomputing evicted 16k prefill tries from scratch on the GPU causes Mean TTFT to spike up to $22.06\text{s}$ at 10 QPS and $15.98\text{s}$ at 20 QPS. Enabling L3 Managed Lustre restores evicted tries from shared storage at high speed, cutting Mean TTFT by **up to -79.8%**, cutting P90 TTFT by **up to -66.0%**, and increasing total throughput by **+24.8% (from 164,299 tok/s to 205,037 tok/s)** at 20 QPS.

| Configuration | Target Rate | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline SGLang + CPU offloading** *(No L3)* | 10 QPS | 22.06 | 40.23 | 38.68 | 52.46 | 89,674 |
| **SGLang + CPU offloading + Lustre** *(With L3)* | 10 QPS | **4.46 (-79.8%)** | **13.67 (-66.0%)** | **18.02 (-53.4%)** | **31.41 (-40.1%)** | **111,939 (+24.8%)** |
| **Baseline SGLang + CPU offloading** *(No L3)* | 20 QPS | 15.98 | 42.04 | 29.66 | 57.31 | 164,299 |
| **SGLang + CPU offloading + Lustre** *(With L3)* | 20 QPS | **7.06 (-55.8%)** | **20.51 (-51.2%)** | **18.19 (-38.7%)** | **34.50 (-39.8%)** | **205,037 (+24.8%)** |

