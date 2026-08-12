# Qwen/Qwen3-32B CPU Offloading Benchmark (4×Intel B60 XPU)

The benchmark runs on 4 × Intel B60 XPUs (24 GB each), on a single model server with TP=4, using Qwen3-32B on the vLLM XPU runtime.

All results show the effect of enabling prefix-cache offloading relative to a VRAM-only configuration, under a high-cache scenario where the working set exceeds VRAM but fits within VRAM + CPU RAM.

* **Workload**: 60 prefix groups, 5 prompts per group, system prompt length of 3,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **VRAM Cache Size (Total)**: 67,584 tokens (16.6 GiB, 4.14 GiB/GPU × 4, `gpu-memory-utilization=0.85`).
* **CPU Cache Size (Total)**: ~409,600 tokens (100 GiB, `cpu_bytes_to_use=107374182400`).
* **Workload Unique Cache (Working Set)**: 333,600 tokens (~4.9× the VRAM cache).

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **1.0 QPS** | VRAM-only | 6.60 | 11.61 | 34.49 | 42.17 | 150.4 |
| | VRAM + CPU RAM | 9.24 (+40.0%) | 18.57 (+59.9%) | 35.08 (+1.7%) | 38.69 (-8.3%) | 173.6 (+15.5%) |
| **2.0 QPS** | VRAM-only | 52.79 | 96.90 | 81.89 | 123.85 | 159.8 |
| | VRAM + CPU RAM | 18.07 (-65.8%) | 39.27 (-59.5%) | 37.66 (-54.0%) | 56.81 (-54.1%) | 241.4 (+51.1%) |
| **3.0 QPS** | VRAM-only | 94.65 | 175.46 | 123.34 | 203.57 | 163.4 |
| | VRAM + CPU RAM | 36.57 (-61.4%) | 72.77 (-58.5%) | 54.48 (-55.8%) | 90.38 (-55.6%) | 272.6 (+66.8%) |
| **4.0 QPS** | VRAM-only | 140.91 | 250.03 | 169.62 | 275.84 | 178.7 |
| | VRAM + CPU RAM | 62.85 (-55.4%) | 116.55 (-53.4%) | 80.71 (-52.4%) | 135.53 (-50.9%) | 286.2 (+60.2%) |

## Reproducing

Deploy the model server with the [Intel XPU deploy steps](../README.md#2-deploy-the-model-server) — `modelserver/xpu/vllm/native/cpu/base` for the offloading run and `modelserver/xpu/vllm/base` for the VRAM-only baseline — then drive both with an `inference-perf` `shared_prefix` workload shaped as below (the XPU equivalent of this guide's dedicated profile):

```yaml
load:
  type: poisson
  interval: 60.0
  stages:
    - { rate: 1.0, duration: 60 }
    - { rate: 2.0, duration: 60 }
    - { rate: 3.0, duration: 60 }
    - { rate: 4.0, duration: 60 }
api:
  type: completion
  streaming: true
data:
  type: shared_prefix
  shared_prefix:
    num_groups: 60              # 60 unique system prompt groups
    num_prompts_per_group: 5    # 5 prompts per group
    system_prompt_len: 3000     # 3K tokens — moderate cache pressure
    question_len: 256           # per-prompt question length
    output_len: 256             # per-prompt output length
    enable_multi_turn_chat: false
```
