# Well-Lit Path: Wide Expert Parallelism (EP/DP)

## Overview

This guide demonstrates how to deploy large Mixture-of-Experts (MoE) models with vLLM's P/D disaggregation support and NIXL in a wide expert-parallel pattern. Wide Expert Parallelism distributes experts across multiple nodes while keeping attention data-parallel, increasing available KV cache space and throughput for very large models.

Wide EP has two deployment variants. In both variants, llm-d Router, Gateway API Inference Extension (GAIE), and EPP handle request routing and P/D scheduling. The variants differ in the Kubernetes workload controller that creates and coordinates the model-server pods.

## Choosing a Variant

### LeaderWorkerSet (LWS)

Use the [LWS variant](README-lws.md) when you want the current Kubernetes-native multi-host path for vLLM DP/EP deployments. The LWS guide is validated on 32-GPU H200 and B200 configurations with InfiniBand or RoCE networking.

The LWS variant deploys `deepseek-ai/DeepSeek-R1-0528` with:

* 1 DP=16 prefill worker
* 1 DP=16 decode worker

### Grove

Use the [Grove variant](README-grove.md) when deploying wide-EP on NVIDIA GB200 hardware with Multi-Node NVLink (MNNVL). Grove is the orchestration path for multi-component inference systems where the controller needs to reason about the whole service, not independent pods.

The Grove capabilities most relevant to Wide EP are:

* Topology-aware placement for keeping workers in the right accelerator and network domains.
* Hierarchical gang scheduling so prefill and decode units come up as functional sets.
* Coordinated scaling, lifecycle management, and recovery across prefill/decode roles.
* MNNVL-aware orchestration, including Auto-MNNVL support for GB200 deployments.
* Coherent rolling updates, which roll compatible components together and preserve balanced serving capacity during upgrades.

The Grove guide is validated on GB200 hardware and deploys coordinated prefill and decode roles while the llm-d EPP continues to route traffic through the same P/D architecture.

## Next Steps

* [Wide EP with LeaderWorkerSet](README-lws.md)
* [Wide EP with Grove](README-grove.md)
