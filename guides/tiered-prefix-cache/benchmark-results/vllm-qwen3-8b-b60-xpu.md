# Qwen/Qwen3-8B CPU Offloading Benchmark (Intel B60 XPU)

The benchmark runs on a single Intel B60 XPU using Qwen3-8B, comparing a VRAM-only configuration against the vLLM native `OffloadingConnector` with a 20 GiB CPU RAM tier.

All results show the effect of enabling prefix-cache offloading relative to a VRAM-only configuration, under a high-cache scenario where the working set exceeds VRAM but fits within VRAM + CPU RAM.

* **Workload**: 15 prefix groups, 5 prompts per group, system prompt length of 4,000 tokens, question length of 256 tokens, output length of 256 tokens.
* **GPU Cache Size**: 49,856 tokens (6.85 GiB).
* **CPU Cache Size (OffloadingConnector)**: ~291,000 tokens (20 GiB, `cpu_bytes_to_use=21474836480`).
* **Workload Unique Cache (Working Set)**: 98,400 tokens (1.97× the GPU cache).

| Target Rate | Configuration | Mean TTFT (s) | P90 TTFT (s) | Mean E2E Latency (s) | P90 E2E Latency (s) | Throughput (tok/s) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **1.0 QPS** | VRAM-only | 8.35 | 13.52 | 27.87 | 33.36 | 2,720.5 |
| | VRAM + CPU RAM | 8.09 (-3.1%) | 13.44 (-0.6%) | 26.56 (-4.7%) | 32.27 (-3.3%) | 2,881.1 (+5.9%) |
| **1.5 QPS** | VRAM-only | 28.01 | 52.60 | 47.51 | 68.45 | 3,113.4 |
| | VRAM + CPU RAM | 11.27 (-59.8%) | 21.94 (-58.3%) | 27.02 (-43.1%) | 37.82 (-44.8%) | 3,632.9 (+16.7%) |
| **2.0 QPS** | VRAM-only | 44.50 | 88.56 | 63.81 | 106.57 | 3,146.4 |
| | VRAM + CPU RAM | 36.21 (-18.6%) | 66.27 (-25.2%) | 52.03 (-18.5%) | 81.17 (-23.8%) | 3,929.9 (+24.9%) |
| **3.0 QPS** | VRAM-only | 83.80 | 152.39 | 103.30 | 171.08 | 3,078.6 |
| | VRAM + CPU RAM | 61.50 (-26.6%) | 119.36 (-21.7%) | 77.17 (-25.3%) | 134.75 (-21.2%) | 4,033.9 (+31.0%) |

## Reproducing

Deploy the model server with the [Intel XPU deploy steps](../README.md#2-deploy-the-model-server) — `modelserver/xpu/vllm/native/cpu/base` for the offloading run and `modelserver/xpu/vllm/base` for the VRAM-only baseline — then drive both with an `inference-perf` `shared_prefix` workload shaped as below (the XPU equivalent of this guide's dedicated profile):

```yaml
load:
  type: poisson
  interval: 60.0
  stages:
    - { rate: 1.0, duration: 60 }
    - { rate: 1.5, duration: 60 }
    - { rate: 2.0, duration: 60 }
    - { rate: 3.0, duration: 60 }
api:
  type: completion
  streaming: true
data:
  type: shared_prefix
  shared_prefix:
    num_groups: 15              # 15 unique system prompt groups
    num_prompts_per_group: 5    # 5 prompts per group
    system_prompt_len: 4000     # 4K tokens — moderate cache pressure
    question_len: 256           # per-prompt question length
    output_len: 256             # per-prompt output length
    enable_multi_turn_chat: false
```
