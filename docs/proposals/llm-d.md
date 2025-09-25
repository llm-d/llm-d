# llm-d: Kubernetes-native Distributed Inference at Scale

## Summary

Provide a well-lit path for anyone to serve large language models (LLMs) at scale, with the fastest
time-to-value and competitive performance per dollar for most models across most hardware
accelerators. Reduce operational toil for workload owners and inference platform teams by cleanly
integrating with Kubernetes and composing with existing infrastructure choices. Work deeply with
vLLM - the model server with the broadest ecosystem and most accessible extensibility - to rapidly
enable new distributed inference protocols.

## Motivation

Generative AI inference serving for large language models (LLMs) is complex at scale, and the key techniques enabling that scale are broadly understood but sparsely implemented and yield high operational toil. 

A significant fraction of accelerators that host LLM inference run atop Kubernetes and are managed by inference platform teams who lack a well-lit path to deploy, scale, and customize efficient serving. These teams also seek high capacity utilization of their general purpose models across multiple client workloads including chat, summarization, search, agents, and emerging multimodal applications, all of which exhibit high variance in cost, tolerance of latency, and operational priority.

The high cost of emerging prompt-heavy use cases means that many primary workload serving deployments must optimize multiple parts of the stack, especially prefix caching, to reach both latency and cost objectives. Workload authors need the flexibility to shape their architecture from standard components that do not limit future growth.

### Goals

llm-d is successful if it:

* Provides well-lit paths for anyone to serve LLMs at scale
* Brings ML ecosystem expertise into production-ready components for high scale serving
* Provides vLLM-native protocols for distributed inference across multiple accelerator families
* Offers an extensible and flexible inference scheduler to balance traffic
* Supports multiple emerging LLM workloads (agents, multimodal, RAG/search) with clear reference architectures
* Composes with existing Kubernetes infrastructure choices
* Is not opinionated about model server deployment orchestration and model server lifecycle
* Is reliably and consistently tested for performance in our development and testing and in end-user production 

### Non-Goals

* Prioritize non-Transformer model architectures (initially)
* Fork upstream repositories or carry unmerged upstream changes
* Control the exact configuration of the end-user vLLM deployments

## Proposal

The `llm-d` project will start with the [Kubernetes Inference Gateway project (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension) and the [vLLM model server](https://github.com/vllm-project/vllm) ecosystem to enable the four primary high-scale techniques:

* Tiered prefix cache hierarchy to improve request latency and throughput
* Disaggregated serving to reduce time-to-first-token latency
* LLM-optimized load balancing for better tail latency and workload prioritization and fairness
* Autoscaling for better accelerator efficiency over different hardware and serving configurations

The three initial layers of the runtime infrastructure are:

* Inference Scheduler - apply Kubernetes-native model routing, handle flow control, and orchestrate disaggregation
* vLLM - support point-to-point disaggregated serving as a native protocol over multiple hardware architectures
* Remote Prefix Cache - separate the operational scaling of replicas from the achievable hit rate

The project will measure success against:

* Achieved scale and performance on key distributed inference workloads
* Efficiency of serving (perf/$ at target latency)
* Reduction of operational toil, especially with increasing workload density

The key design choices in `llm-d` are:

1. Assume multiple workloads are consuming a shared model server pool and standardize those workload APIs via Kubernetes
2. Leverage scheduler-directed RPC for disaggregated serving to allow latency and throughput to be traded off dynamically
3. Strongly separate in-memory and scheduler-directed prefix caching per replica from durable disaggregated prefix caching to prevent replicas from becoming stateful and to support key sets larger than replica working memory
4. Use NIXL to abstract high-performance point-to-point KV-cache transfer without a metadata side channel
5. Adapt to existing user deployment patterns and infrastructure vs. creating a single monolithic solution
6. Integrate APIs and core capabilities into upstreams to enforce modularity
7. Support multiple accelerator hardware architectures with a single set of abstractions, including multiple GPU vendors and systolic architectures like Google TPUs

### User Stories (Optional)

#### Story 1

As an inference platform team, I can rapidly deploy a shared-nothing serving stack for most LLMs that can be scaled up with prefix caching on both HBM and host memory, fast prefix-cache-aware routing, and independently scalable (often called `xPyD`) disaggregated serving. The stack has clear operational metrics and I can measure a significant throughput improvement over round-robin load balancing. The operational and reliability aspects of my stack vary across accelerator hardware only on characteristics tied to the intrinsic hardware, host, and networking configuration.

#### Story 2

As an inference platform team, I can deploy the [DeepSeek R1 inference system](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) in a full xPyD architecture and leverage expert parallelism at peak performance, while being able to reconfigure the stack to a diverse range of traffic distributions using standard Kubernetes primitives.

#### Story 3

As an inference platform team, I can autoscale disaggregated serving roles, different accelerator hardware, and tuned vLLM replicas within a single serving pool to match the current mix of workload traffic, reducing my overall cost to serve and expanding the range of usable capacity.

## Design Details

The [unified architecture diagram](https://docs.google.com/drawings/d/1PNGNsicSFiFJjSBThgg6zhxAQMdJp9LqqExQ-8lmmUY/edit) below shows all of the key components of the system, as well as the basic flow for request flow.

![Architecture diagram](../assets/images/llm-d-arch-initial-large.svg)

Our current north star designs lay out the initial scope (join llm-d-contributors@googlegroups.com to comment). They will be converted into project proposals:

* [vLLM-Optimized Inference Scheduler](https://docs.google.com/document/d/1kE1LY8OVjiOgKVD9-9Po96HODbTIbgHp4qgvw06BCOc/edit)
* [Disaggregated Serving with vLLM](https://docs.google.com/document/d/1FNN5snmipaTxEA1FGEeSH7Z_kEqskouKD1XYhVyTHr8/edit)
* [Prefix Cache Hierarchy](https://docs.google.com/document/d/1d-jKVHpTJ_tkvy6Pfbl3q2FM59NpfnqPAh__Uz_bEZ8/edit?tab=t.0#heading=h.6qazyl873259)
* [Variant Autoscaling over Hardware, Workload, and Traffic](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58/edit)

### Components

llm-d streamlines deployment and integration of the following components:

1. An **inference scheduler** directing traffic to model servers
    1. Integrates with the Kubernetes [inference gateway API project](https://gateway-api-inference-extension.sigs.k8s.io/) to have Kubernetes control plane API
    2. Performs model routing and rollout, flow control, KV- and prefix-cache-aware load balancing
    3. Balances traffic to the optimal model server based on the request, workload type, and current load
2. **vLLM model servers** deployed onto Kubernetes
    1. In single-host or multi-host (using [LeaderWorkerSets](https://lws.sigs.k8s.io/) and [Ray](https://docs.vllm.ai/en/latest/serving/distributed_serving.html#running-vllm-on-multiple-nodes) as best practice) configurations
    2. With native support for disaggregated serving and optional curated plugins for advanced features
    3. Using project-recommended defaults or highly customized user settings
    4. May be deployed in multiple deployment **variants** (hardware, software, topology) that offer different performance tradeoffs
    5. Can be rapidly repurposed as load shifts and started/restarted to reduce wasted capacity
    6. Can dynamically load new LoRA adapters or even new models with low configuration/coordination
3. vLLM default prefix caching and zero or more **prefix cache integrations**
    1. At least one remote prefix cache option
    2. At least one prefix cache option using the local SSD drives for each replica efficiently
4. A **variant autoscaler** working with the inference scheduler and Kubernetes horizontal pod autoscaling
    1. Can reassign prefill and decode roles between model server instances dynamically
    2. Is aware of multiple deployment variants and their performance and can optimize across them
    3. Can perform more advanced bucketization of traffic on latency or throughput objectives

### APIs

llm-d intends to drive the following APIs in our upstreams:

1. vLLM public workload APIs to support inference scheduler-driven disaggregated serving
2. vLLM management APIs to support rapid reconfiguration of vLLM where appropriate
3. vLLM internal APIs to support point-to-point disaggregated serving, remote prefix cache, and testing
4. Kubernetes Gateway APIs to support static or dynamic tuning of disaggregation
5. Kubernetes Gateway APIs to enable latency or throughput optimization and autoscaling
6. vLLM/[LMCache](https://lmcache.ai) remote prefix cache APIs that are interoperable across implementations

llm-d may incubate new Kubernetes APIs via custom resources or new vLLM APIs, but our primary path is to land APIs upstream.

### Dependencies

llm-d intends to use [NIXL](https://github.com/ai-dynamo/nixl) to optimize GPU-originating and GPU-terminating transfers. A follow-on proposal will identify gaps across accelerators and in host-to-host scenarios and recommend a solution.

## Alternatives

### Use NVIDIA Dynamo

NVIDIA Dynamo offers an excellent integrated stack for low-latency and high-scale serving. llm-d intends to work closely with the Dynamo team on integrating components of Dynamo into the operational framework of Kubernetes. We are prioritizing the inference scheduler as the key component to enhance Dynamo.

Unlike Dynamo, llm-d:
* Prefers to make the prefill/decode disaggregation decision within the scheduler to more precisely control placement and latency vs. throughput tradeoffs
* Uses RPC for disaggregation rather than an [async queue in the Distributed Runtime](https://docs.nvidia.com/dynamo/latest/architecture/distributed_runtime.html) to provide stronger cancellation semantics
* Prioritizes a strong operational boundary between in-memory prefix cache tiers and local or remote storage tiers rather than a unified memory API like the [KV Block Manager](https://docs.nvidia.com/dynamo/latest/architecture/kvbm_intro.html)

### Use AIBrix

AIBrix provides a strong research-focused and fast-iterating integrated serving platform. llm-d intends to work closely with the AIBrix team to leverage their experience in autoscaling and serving to standardize components and best practices.

Unlike AIBrix, llm-d:
* Prefers to adapt serving to existing user infrastructure vs. providing an opinionated serving platform
* Prioritizes standardizing Kubernetes upstream APIs in the Gateway ecosystem to drive alignment across many deployments

### Use Production Stack

production-stack is the easiest way to deploy vLLM on Kubernetes. llm-d intends to work closely with the production-stack team to find common components and patterns to integrate, especially around prefix cache configuration.

Unlike production-stack, llm-d:
* Is focused on the needs of large-scale production serving and expects users to have significant opinions about the rest of the infrastructure

### Use KServe

KServe offers a comprehensive platform for teams running large numbers of traditional and generative models on Kubernetes. Consider KServe when you have high numbers of model deployments or if you have many teams that need distinct deployments of models. llm-d intends to expose [large model optimizations into KServe](https://docs.google.com/document/d/11ZQJ2VhTc42S9K4yau2dMs3Q3f4jqWJL_7Sq14C3hzY/edit?usp=sharing).

Unlike KServe, llm-d:
* Is focused on reducing the operational friction for serving single workloads and enabling large-model-as-a-service offerings with core capabilities rather than offering an integrated and broad platform
* Does not orchestrate inference workloads directly
* Does not attempt to solve problems related to traditional ML serving or models that consume less than 1 accelerator