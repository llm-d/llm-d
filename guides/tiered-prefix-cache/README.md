# Well-lit Path: Prefix Cache Offloading

## Overview

Efficient caching of prefix computation states to avoid recomputation is crucial for boosting Large Language Model (LLM) inference performance such as Time to First Token (TTFT) and overall throughput, as well as reducing the cost. For the self-attention mechanism, the generation of the next token leverages the prefix Key & Value (KV) tensors. For State Space Model (SSM) models such as mamba models, reusing cache of its SSM states of prefix locations also saves computation for the next token. In this guide we use the term "prefix cache" to refer to the cache of computation states in the prefix tokens of a target token which includes the caching of prefix KV tensors and other forms of caches. The prefix aware request scheduling optimizations in the [inference scheduling](../inference-scheduling/README.md) also applies here.

State of the art inference engines already implement native prefix cache reuse across requests in accelerator High-Bandwidth Memory (HBM), but in most serving environments HBM is already a constrained resource. To increase the amount of available memory beyond HBM requires more cache storage, driving the need for offloading prefix cache from HBM to more cost effective storage options such as CPU RAM.

This well-lit path offers multiple sub-guides per the cache storage type, either used standalone, or combined with other storage types in a tiered cache hierarchy. It also provides high level guidance on their suitability per workload, and makes recommendations about selecting and configuring a prefix cache offloading implementation.

## Storage Types

### CPU RAM

Enabling prefix cache offloading to CPU is recommended for the following reasons:

* Little operational overhead.
* There are usually more CPU RAM storage available than accelerator HBM on the host offering much larger cache capacity.
* CPU - accelerator transfer is faster than recomputation for most cases.
* (WIP) Prefix cache storage tier aware inference scheduling makes smart decisions based on cache tier (accelerator HBM vs. CPU RAM).

In low cache size scenario where HBM is primarily used, async CPU offloading should incur little overhead. In high cache size scenario loading cache from CPU RAM offers significantly higher cache hit and thus better performance than HBM only.

See the [CPU offloading guide](./cpu/README.md) to learn how to enable CPU RAM offloading with llm-d.

### Local Disk

Utilizing local disk storage can significantly increase the cache capacity. However disks are typically significantly slower than CPU RAM. 

Consider this when:

* your workload can tolerate the latency overhead.
* the cache capacity of local disks is sufficient for your use case.

Otherwise we recommend a shared storage because it:

* offers cache sharing between instances,
* has more options to choose from to get a good tradeoff between cost and performance,
* offers significantly larger capacity.

### Shared Storage

Offloading prefix cache to a shared(remote) storage offers the following benefits:

* Massive storage capacity independent of the inference engine deployment capacity.
* Seamlessly share prefix cache across inference engine replicas and restarts.

However, it adds both operational and performance overhead which depends on the characteristics (such as latency and throughput) of the storage system. Thus the decision of offloading to a shared storage system needs careful consideration.

Consider a shared storage option when at least one of the following applies:

* Large cache capacity requirement beyond HBM + CPU RAM.
* Long input size (>10k) and high cache hits.
* Frequent cache migration needs (e.g., model or engine rollouts).

### P2P Cache Sharing

A P2P network can be formed between the inference engine instances to share caches in HBMs or CPU memory. It enables more cache sharing without needing additional storage resources. However this strategy adds operational overhead, and potential contention between model parallelism traffic such as tensor parallelism. We will add more recommendations in the following releases.

## Cache Tiering

Generally multiple cache tiers can be applied ordered by their cache read/write latencies, allowing frequently accessed caches to stay as close as possible to the accelerator, and large or less frequently accessed caches to be offloaded to slower tiers. We recommend always setting up HBM and CPU RAM tiers, and consider a third or fourth tier when your cache needs goes beyond HBM + CPU RAM.
