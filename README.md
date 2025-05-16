<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/assets/images/llm-d-logo.png" width=38%>
  </picture>
</p>

<h3 align="center">
Kubernetes-Native Distributed Inference at Scale
</h3>

 [![Documentation](https://img.shields.io/badge/Documentation-8A2BE2?logo=read-the-docs&logoColor=%23ffffff&color=%231BC070)](https://...) [![License](https://img.shields.io/github/license/llm-d/llm-d.svg)](https://github.com/llm-d/llm-d/blob/main/LICENSE) 
  <a href="...">
    <img alt="Join Slack" src="https://img.shields.io/badge/Join%20Slack-blue?logo=slack">
  </a>

Latest News 🔥
- [2025-05] Coreweave, Google, IBM Research, NVIDIA, and Red Hat officially launched `llm-d`. Check out [our blog post - UPDATE]() and [press release - UPDATE]().

## 📄 About

`llm-d` is a Kubernetes-native distributed inference serving stack - a well-lit path for anyone to serve at scale, with the fastest time-to-value and competitive performance per dollar for most models across most hardware accelerators.

With `llm-d` users can operationalize GenAI deployments with a modular, high-performance, end-to-end solution that leverages the latest distributed inference optimizations like KV-cache aware routing and disaggregated serving, co-designed and integrated with the Kubernetes operational tooling in [Inference Gateway (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension).

Developed by leaders in the Hardware Accelerator, Kuberentes, and vLLM ecosystems, `llm-d` is a community-driven, Apache-2 licensed project with an open development model.

## 🧱 Architecture

`llm-d` adopts a layered architecture on top of industry-standard open technologies: vLLM, Kubernetes, and Inference Gateway.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Arch" src="./docs/assets/images/llm-d-arch.svg" width=95%>
  </picture>
</p>

Key features of `llm-d` include:
- **vLLM-Optimized Inference Scheduler:** `llm-d` builds on IGW's pattern for customizable “smart” load-balancing via the Endpoint Picker Protocol (EPP) to define vLLM-optimized scheduling. Leveraging operational telemetry exposed by vLLM, Scheduler implements filtering and scoring algorithms necessary to make decisions with P/D-, KV-cache-, SLA-, and load-awareness. Advanced teams can implement their own scorers and filterers to further customize for their use cases, while benefiting from othr features in IGW, like flow control and latency-aware balancing. [For more details, see our Northstar design](https://docs.google.com/document/d/1kE1LY8OVjiOgKVD9-9Po96HODbTIbgHp4qgvw06BCOc/edit?tab=t.0#heading=h.4rgkvvo5gnle)

- **Disaggregated Serving with vLLM:** `llm-d` leverages vLLM’s recently enabled support for disaggregated serving via a pluggable KV Connector API to run prefill and decode on independent instances, using high-performance transport libraries like NVIDIA’s NIXL. In `llm-d`, we plan to support latency-optimized implementation using fast interconnects (IB, RDMA, ICI) and throughput optimized implementation using data-center netwokring. [For more details, see our Northstar design](https://docs.google.com/document/d/1FNN5snmipaTxEA1FGEeSH7Z_kEqskouKD1XYhVyTHr8/edit?tab=t.0)

- **Disaggregated Prefix Caching with vLLM:** `llm-d` uses the same vLLM KV connector API used in disaggregated serving to provide a pluggable cache for previous calculations, including offloading KVs to host, remote storage, and systems like LMCache. In llm-d, we plan to support two KV caching schemes. *Independent* (north-soutch) caching with basic offloading to host memory and disk, providing a zero operational cost mechanism that utilizes all system resources. *Shared* (east-west) caching with KV transfer between instances and shared storage with global indexing, providing potential for higher performance at the cost of a more operationally complex system. [For more details, see our Northstar design](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58/edit?tab=t.0)

- **Variant Autoscaling over Hardware, Workload, and Traffic** (🚧): We plan to implement a traffic- and hardware-aware autoscaler that (a) measures the capacity of each model server instance, (b) derive a load function that takes into account different request shapes and QoS, and (c) asseses recent traffic mix (QPS, QoS, and shapes)
Using the recent traffic mix to calculate the optimal mix of instances to handle prefill, decode, and latency-tolerant requests, enabling use of HPA for SLO-level efficiency. [For more details, see our Northstar design](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58/edit?tab=t.0)


## 🚀 Getting Started

`llm-d` can be installed as a full solution, customizing enabled features, or through its individual components for experimentation.

### Deploying as as solution

llm-d's deployer can be used to that installed it as a solution using a single Helm chart on Kubernetes.

> [!TIP]
> See the guided expericience with our [quickstart](https://github.com/neuralmagic/llm-d-deployer/blob/main/quickstart/README.md).

### Experimenting and developing with llm-d

llm-d is repo is a metaproject with subcomponents can that can be cloned indvidually. 

To clone all the components:
```
    git clone --recurse-submodules https://github.com/llm-d/llm-d.git 
``` 

> [!TIP]
> As a customizatoin example, see [here]() a template for adding a scheduler scorer.

## 📦 Releases

Visit our [GitHub Releases page](https://github.com/llm-d/llm-d/releases) and review the release notes to stay updated with the latest releases.


## Contribute

### Slack
`llm-d` uses Slack to discuss development across organizations. Please join to discuss major features.
- [Link to Slack - UPDATE](https://...)

### Weekly Sync
`llm-d` host a weekly contributors sync on Wednesdays at 1230pm ET. We run a standup-style session:
- [Meeting Details](https://calendar.google.com/calendar/event?action=TEMPLATE&tmeid=NG9yZ3AyYTN0N3VlaW01b21xbWV2c21uNjRfMjAyNTA1MjhUMTYzMDAwWiByb2JzaGF3QHJlZGhhdC5jb20&tmsrc=robshaw%40redhat.com&scp=ALL)

### Guidelines

We appreciate contributions to the code, examples, integrations, documentation, bug reports, and feature requests! Your feedback and involvement are crucial in helping llm-d grow and improve. Below are some ways you can get involved:

- [**DEVELOPING**](https://github.com/llm-d/llm-d/blob/main/DEVELOPING.md) - Development guide for setting up your environment and making contributions.
- [**CONTRIBUTING**](https://github.com/llm-d/llm-d/blob/main/CONTRIBUTING.md) - Guidelines for contributing to the project, including code standards, pull request processes, and more.
- [**CODE_OF_CONDUCT**](https://github.com/llm-d/llm-d/blob/main/CODE_OF_CONDUCT.md) - Our expectations for community behavior to ensure a welcoming and inclusive environment.


## License

This project is licensed under Apache License 2.0. See the LICENSE file for details.
