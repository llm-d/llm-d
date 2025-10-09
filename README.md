<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/assets/images/llm-d-logo.png" width=37%>
  </picture>
</p>

<h2 align="center">
Achieve SOTA Inference Performance On Any Accelerator
</h2>

 [![Documentation](https://img.shields.io/badge/Documentation-8A2BE2?logo=readthedocs&logoColor=white&color=1BC070)](https://www.llm-d.ai)
 [![Release Status](https://img.shields.io/badge/Version-0.3-yellow)](https://github.com/llm-d/llm-d/releases)
 [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
 [![Join Slack](https://img.shields.io/badge/Join_Slack-blue?logo=slack)](https://llm-d.ai/slack)

Latest News 🔥
- [2025-10] Our [v0.3](https://llm-d.ai/blog/llm-d-v0.3-expanded-hardware-faster-perf-and-igw-ga) release delivers Intel XPU and Google TPU support, TCP and RDMA over RoCE tested with disaggregation, new experimental predicted latency balancing for up to 3x better P90 latency on long prefill, DeepSeek Expert Parallel serving reaching 2.7k output tokens/s/gpu on H200, and integrates the Inference Gateway v1.0 GA release.
- [2025-08] Read more about the [intelligent inference scheduler](https://llm-d.ai/blog/intelligent-inference-scheduling-with-llm-d), including a deep dive on how different balancing techniques are composed to improve throughput without overloading replicas.

## 📄 About

llm-d is a Kubernetes-native distributed inference serving stack providing well-lit paths for anyone to serve large generative AI models at scale. We help you achieve the fastest "time to state-of-the-art (SOTA) performance" for key OSS models across most hardware accelerators and infrastructure providers.

Our [well-lit paths](./guides/README.md) provide tested and benchmarked recipes and Helm charts to start serving quickly with best practices common to production deployments. They are extensible and customizable for particulars of your models and use cases, using popular open source components like Kubernetes, Envoy proxy, NIXL, and vLLM. Our intent is to eliminate the heavy lifting common in tuning and deploying inference at scale.

We currently offer three tested and benchmarked paths to help deploying large models:

1. [Intelligent Inference Scheduling](./guides/inference-scheduling/README.md) - Deploy [vLLM](https://docs.vllm.ai) behind the [Inference Gateway (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension) to decrease serving latency and increase throughput with [predicted latency balancing (experimental)](./guides/predicted-latency-based-scheduling), [precise prefix-cache aware routing](./guides/precise-prefix-cache-aware/README.md), and [customizable scheduling policies](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md).
2. [Prefill/Decode Disaggregation](./guides/pd-disaggregation/README.md) - Reduce time to first token (TTFT) and get more predictable time per output token (TPOT) by splitting inference into prefill servers handling prompts and decode servers handling responses, primarily on large models such as Llama-70B and when processing very long prompts.
3. [Wide Expert-Parallelism](./guides/wide-ep-lws/README.md) - Deploy very large Mixture-of-Experts (MoE) models like [DeepSeek-R1](https://github.com/vllm-project/vllm/issues/16037) and significantly reduce end-to-end latency and increase throughput by scaling up with [Data Parallelism and Expert Parallelism](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment.html) over fast accelerator networks.

### Hardware Support

llm-d directly tests and validates multiple accelerator types including NVIDIA GPUs, AMD GPUs, Google TPUs, and Intel XPUs and provides common operational patterns to improve production reliability.

See the [accelerator docs](./docs/accelerators/README.md) for points of contact for more details about the accelerators, networks, and configurations tested and our [roadmap](https://github.com/llm-d/llm-d/issues/146) for what is coming next.

### What is in scope for llm-d

`llm-d` currently targets improving the production serving experience around:

* Online serving and online batch of Generative models running in PyTorch or JAX
  * Large language models (LLMs) with 1 billion or more parameters
  * Using most or all of the capacity of one or more hardware accelerators
  * Running in throughput, latency, or multiple-objective configurations
* On recent generation datacenter-class accelerators
  * NVIDIA A100 / L4 or newer
  * AMD MI250 or newer
  * Google TPU v5e or newer
  * Intel Data Center GPU Max (XPU/Ponte Vecchio) series or newer
* With extremely fast accelerator interconnect and datacenter networking
  * 600-16,000 Gbps per accelerator NVLINK on host or across narrow domains like NVL72
  * 1,600-5,000 Gbps per chip TPU OCS links within TPU pods
  * 100-1,600 Gbps per host datacenter networking across broad (>128 host) domains
* Kubernetes 1.29+ as the hardware orchestrator
  * in large (100-100k node) reserved cloud capacity or datacenters, often overlapping with AI batch and training
  * in medium (10-1k node) cloud deployments with a mix of reserved, on-demand, or spot capacity
  * in small (1-10 node) test and qualification environments with a static footprint, often time shared

Our upstream projects – particularly vLLM and Kubernetes – support a broader array of models, accelerators, and networks that may also benefit from our work, but we concentrate on optimizing and standardizing the operational and automation challenges of the leading edge inference workloads.

## 🧱 Architecture

llm-d accelerates distributed inference by integrating industry-standard open technologies: vLLM as default model server and engine, Inference Gateway as request scheduler and balancer, and Kubernetes as infrastructure orchestrator and workload control plane.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Arch" src="./docs/assets/images/llm-d-arch.svg" width=95%>
  </picture>
</p>

Key features of llm-d include:

- **vLLM-Optimized Inference Scheduler:** llm-d builds on IGW's pattern of leveraging the Envoy proxy and its extensible balancing policies to make customizable “smart” load-balancing decisions specifically for LLMs. Leveraging operational telemetry, the Inference Scheduler implements the filtering and scoring algorithms to make decisions with P/D-, KV-cache-, SLA-, and load-awareness. Advanced users can implement their own scorers to further customize the algorithm while benefiting from IGW features like flow control and latency-aware balancing. [See our Northstar design](https://docs.google.com/document/d/1kE1LY8OVjiOgKVD9-9Po96HODbTIbgHp4qgvw06BCOc/edit?tab=t.0#heading=h.4rgkvvo5gnle)

- **Disaggregated Serving with vLLM:** llm-d orchestrates prefill and decode phases onto  independent instances - the scheduler decides which instances should receive a given request, and the transaction is coordinated via a sidecar alongside decode instances. The sidecar instructs vLLM to provide point to point KV cache transfer over fast interconnects (IB/RoCE RDMA, TPU ICI, and DCN) via NIXL. [See our Northstar design](https://docs.google.com/document/d/1FNN5snmipaTxEA1FGEeSH7Z_kEqskouKD1XYhVyTHr8/edit?tab=t.0)

- **Disaggregated Prefix Caching :** llm-d uses vLLM's KVConnector abstraction to configure a pluggable KV cache hierarchy, including offloading KVs to host, remote storage, and systems like LMCache. We plan to support two KV caching schemes. [See our Northstar design](https://docs.google.com/document/d/1d-jKVHpTJ_tkvy6Pfbl3q2FM59NpfnqPAh__Uz_bEZ8/edit?tab=t.0#heading=h.6qazyl873259)
    - *Independent (N/S)* caching with offloading to local memory and disk, providing a zero operational cost mechanism for offloading.
    - *Shared (E/W)* caching with KV transfer between instances and shared storage with global indexing, providing potential for higher performance at the cost of a more operationally complex system.

- **Variant Autoscaling over Hardware, Workload, and Traffic**: A traffic- and hardware-aware autoscaler that (a) measures the capacity of each model server instance, (b) derive a load function that takes into account different request shapes and QoS, and (c) assesses recent traffic mix (QPS, QoS, and shapes) to calculate the optimal mix of instances to handle prefill, decode, and latency-tolerant requests, enabling use of HPA for SLO-level efficiency. [See our Northstar design](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58/edit?tab=t.0)

For more see the [project proposal](./docs/proposals/llm-d.md).

## 🚀 Getting Started

`llm-d` can be installed as a full solution, customizing enabled features, or through its individual components for experimentation.

### Pre-requisites

`llm-d` requires accelerators capable of running large models. Our well-lit paths are focused on datacenter accelerators and networks and issues encountered outside these may not receive the same level of attention.

See the [prerequisites for our guides](./guides/prereq/) for more details on supported hardware, networking, Kubernetes cluster configuration, and client tooling.

### Deploying llm-d

`llm-d` provides Helm charts that deploy the [inference scheduler](https://github.com/llm-d-incubation/llm-d-infra/blob/main/charts/llm-d-infra/README.md#tldr) and a parameterized [deployment of vLLM](https://github.com/llm-d-incubation/llm-d-modelservice/blob/main/README.md#getting-started) that demonstrates [a number of different production configurations](https://github.com/llm-d-incubation/llm-d-modelservice/tree/main/examples).

We bundle these with guides to our [well-lit paths](./guides/README.md) with key decisions, tradeoffs, benchmarks, and recommended configuration.

We suggest the [inference scheduling](./guides/inference-scheduling/README.md) well-lit path if you need a simple, production ready deployment of vLLM with optimized load balancing.

> [!TIP]
> For a more in-depth introduction to llm-d, try our [step-by-step quickstart](./guides/QUICKSTART.md).

### Experimenting and developing with llm-d

`llm-d` is composed of multiple component repositories and derives from both vLLM and Inference Gateway upstreams. Please see the individual repositories for more guidance on development.

## 📦 Releases

Our [guides](./guides/README.md) are living docs and kept current. For details about the Helm charts and component releases, visit our [GitHub Releases page](https://github.com/llm-d/llm-d/releases) to review release notes.

Check out our [roadmap for upcoming releases](https://github.com/llm-d/llm-d/issues/146).

## Contribute

- See [our project overview](PROJECT.md) for more details on our development process and governance.
- Review [our contributing guidelines](CONTRIBUTING.md) for detailed information on how to contribute to the project.
- Join one of our [Special Interest Groups (SIGs)](SIGS.md) to contribute to specific areas of the project and collaborate with domain experts.
- We use Slack to discuss development across organizations. Please join: [Slack](https://llm-d.ai/slack)
- We host a weekly standup for contributors on Wednesdays at 12:30 PM ET, as well as meetings for various SIGs. You can find them in the [shared llm-d calendar](https://red.ht/llm-d-public-calendar)
- We use Google Groups to share architecture diagrams and other content. Please join: [Google Group](https://groups.google.com/g/llm-d-contributors)

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.
