<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/assets/images/llm-d-logo.png" width=38%>
  </picture>
</p>

<h3 align="center">
Kubernetes-Native Distributed Inference at Scale
</h3>

 [![Documentation](https://img.shields.io/badge/Documentation-8A2BE2?logo=readthedocs&logoColor=white&color=1BC070)](https://www.llm-d.ai)
 [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
 [![Join Slack](https://img.shields.io/badge/Join_Slack-blue?logo=slack)](https://inviter.co/llm-d-slack)

Latest News 🔥
- [2025-05] CoreWeave, Google, IBM Research, NVIDIA, and Red Hat launched the llm-d community. Check out [our blog post](https://llm-d.ai/blog/llm-d-announce) and [press release](https://llm-d.ai/blog/llm-d-press-release).

## 📄 About

llm-d is a Kubernetes-native distributed inference serving stack - a well-lit path for anyone to serve large language models at scale, with the fastest time-to-value and competitive performance per dollar for most models across most hardware accelerators.

With llm-d, users can operationalize GenAI deployments with a modular solution that leverages the latest distributed inference optimizations like KV-cache aware routing and disaggregated serving, co-designed and integrated with the Kubernetes operational tooling in [Inference Gateway (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension).

Built by leaders in the Kubernetes and vLLM projects, llm-d is a community-driven, Apache-2 licensed project with an open development model.

## 🧱 Architecture

llm-d adopts a layered architecture on top of industry-standard open technologies: vLLM, Kubernetes, and Inference Gateway.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Arch" src="./docs/assets/images/llm-d-arch.svg" width=95%>
  </picture>
</p>

Key features of llm-d include:

- **vLLM-Optimized Inference Scheduler:** llm-d builds on IGW's pattern for customizable “smart” load-balancing via the Endpoint Picker Protocol (EPP) to define vLLM-optimized scheduling. Leveraging operational telemetry, the Inference Scheduler implements the filtering and scoring algorithms to make decisions with P/D-, KV-cache-, SLA-, and load-awareness. Advanced teams can implement their own scorers to further customize, while benefiting from other features in IGW, like flow control and latency-aware balancing. [See our Northstar design](https://docs.google.com/document/d/1kE1LY8OVjiOgKVD9-9Po96HODbTIbgHp4qgvw06BCOc/edit?tab=t.0#heading=h.4rgkvvo5gnle)

- **Disaggregated Serving with vLLM:** llm-d leverages vLLM’s support for disaggregated serving to run prefill and decode on independent instances, using high-performance transport libraries like NIXL. In llm-d, we plan to support latency-optimized implementation using fast interconnects (IB, RDMA, ICI) and throughput optimized implementation using data-center networking. [See our Northstar design](https://docs.google.com/document/d/1FNN5snmipaTxEA1FGEeSH7Z_kEqskouKD1XYhVyTHr8/edit?tab=t.0)

- **Disaggregated Prefix Caching with vLLM:** llm-d uses vLLM's KVConnector to provide a pluggable KV cache hierarchy, including offloading KVs to host, remote storage, and systems like LMCache. We plan to support two KV caching schemes. [See our Northstar design](https://docs.google.com/document/d/1d-jKVHpTJ_tkvy6Pfbl3q2FM59NpfnqPAh__Uz_bEZ8/edit?tab=t.0#heading=h.6qazyl873259)
    - *Independent (N/S)* caching with offloading to local memory and disk, providing a zero operational cost mechanism for offloading.
    - *Shared (E/W)* caching with KV transfer between instances and shared storage with global indexing, providing potential for higher performance at the cost of a more operationally complex system.

- **Variant Autoscaling over Hardware, Workload, and Traffic** (🚧): We plan to implement a traffic- and hardware-aware autoscaler that (a) measures the capacity of each model server instance, (b) derive a load function that takes into account different request shapes and QoS, and (c) assesses recent traffic mix (QPS, QoS, and shapes) to calculate the optimal mix of instances to handle prefill, decode, and latency-tolerant requests, enabling use of HPA for SLO-level efficiency. [See our Northstar design](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58/edit?tab=t.0)

For more see the [project proposal](./docs/proposals/llm-d.md).

## 🚀 Getting Started

llm-d can be installed as a full solution, customizing enabled features, or through its individual components for experimentation.

### Deploying as as solution

`llm-d`'s deployer can be used to install all main components using a single Helm chart on Kubernetes.

> [!TIP]
> See the guided experience with our [quickstart](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README.md).

### Experimenting and developing with llm-d

`llm-d` is a metaproject composed of subcomponent repositories that can be cloned individually.

To clone all main components:
```
repos="llm-d llm-d-deployer llm-d-inference-scheduler llm-d-kv-cache-manager llm-d-routing-sidecar llm-d-model-service llm-d-benchmark llm-d-inference-sim"; for r in $repos; do git clone https://github.com/llm-d/$r.git; done
``` 

> [!TIP]
> As a customization example, see this [template](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/create_new_filter.md) for adding a custom scheduler filter.

## 📦 Releases

Visit our [GitHub Releases page](https://github.com/llm-d/llm-d-deployer/releases) and review the release notes to stay updated with the latest releases.


## Contribute

- See [our project overview](PROJECT.md) for more details on our development process and governance.
- We use Slack to discuss development across organizations. Please join: [Slack](https://inviter.co/llm-d-slack)
- We host a weekly standup for contributors on Wednesdays at 12:30pm ET. Please join: [Meeting Details](https://calendar.google.com/calendar/u/0?cid=NzA4ZWNlZDY0NDBjYjBkYzA3NjdlZTNhZTk2NWQ2ZTc1Y2U5NTZlMzA5MzhmYTAyZmQ3ZmU1MDJjMDBhNTRiNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t)
- We use Google Groups to share architecture diagrams and other content. Please join: [Google Group](https://groups.google.com/g/llm-d-contributors)

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.
