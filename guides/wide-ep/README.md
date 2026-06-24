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

The Grove variant deploys `nvidia/DeepSeek-R1-NVFP4`.

#### How Grove Orchestrates This Deployment

This deployment utilizes Grove's PodCliqueSet to coordinate 9 nodes as a single logical inference system. The following describes how Grove's primitives map to the requirements of a Wide-EP workload:

- **Hierarchical gang scheduling**: Grove ensures the minimum viable combination of prefill and decode units are scheduled together, preventing resource deadlocks and ensuring service readiness
- **Topology-aware placement**: Grove leverages cluster topology to place pods optimally within NVLink domains, minimizing inter-node latency for KV-cache transfers
- **Multilevel, graceful scaling**: PodClique and PodCliqueScalingGroup primitives enable independent scaling of prefill and decode workers while maintaining correct component ratios and system integrity
- **System-level lifecycle & recovery**: Grove treats multi-component systems as single operational units. Recovery and updates operate on complete service instances, ensuring workers properly reconnect to leaders after a restart and rolling updates preserve network topology
- **Role-aware orchestration**: Defines explicit startup ordering (e.g., ensuring decode leaders are ready before workers) and role-specific configurations within a single declarative PodCliqueSet
- **Native scheduler integration**: Grove automatically translates workload intent into PodGang resources for the [KAI Scheduler](https://github.com/NVIDIA/KAI-Scheduler). This enables the scheduler to execute topology-aware placement, hierarchical queuing, optimizing the entire scheduling cycle by ensuring resources are allocated according to the specific performance and availability requirements of the workload
- **Automatic MNNVL configuration**: Grove abstracts away ComputeDomain and ResourceClaimTemplate complexity - just deploy your PodCliqueSet and Grove handles the rest
- **Coherent updates**: Grove updates compatible prefill and decode components together, preserving version compatibility and balanced serving capacity during upgrades

Grove is designed from the ground up for the unique requirements of AI inference - particularly on next-generation hardware like GB200 where maximizing NVLink fabric utilization is essential for performance.

## Next Steps

* [Wide EP with LeaderWorkerSet](README-lws.md)
* [Wide EP with Grove](README-grove.md)
