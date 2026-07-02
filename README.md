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
 [![Release Status](https://img.shields.io/badge/Version-0.8-yellow)](https://github.com/llm-d/llm-d/releases)
 [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
 [![Join Slack](https://img.shields.io/badge/Join_Slack-blue?logo=slack)](https://llm-d.ai/slack)

llm-d is a high-performance distributed inference serving stack optimized for production deployments on Kubernetes. We help you achieve the fastest "time to state-of-the-art (SOTA) performance" for key OSS large language models across most hardware accelerators and infrastructure providers with well-tested guides and real-world benchmarks.

llm-d is a [Cloud Native Computing Foundation (CNCF)](https://www.cncf.io/) sandbox project, founded by Red Hat, Google Cloud, IBM Research, CoreWeave, and NVIDIA.

## What does llm-d offer to production inference?

Model servers like [vLLM](https://docs.vllm.ai) and [SGLang](https://github.com/sgl-project/sglang) handle efficiently running large language models on accelerators. llm-d provides state-of-the-art orchestration and optimizations above model servers to serve high-scale real-world traffic efficiently and reliably. Our offerings are organized into four core themes:

* **[Intelligent Routing:](https://llm-d.ai/docs/well-lit-paths/optimized-baseline)** Maximize performance with prefix-cache and load-aware balancing, including experimental predicted latency-based scheduling to decrease latency and increase throughput.
* **[Advanced KV-Cache Management:](https://llm-d.ai/docs/well-lit-paths/tiered-prefix-cache)** Increase the effective "working set size" for multi-turn requests with tiered offloading to CPU or disk and precise global indexing of the KV cache state.
* **[Serving Large Models:](https://llm-d.ai/docs/well-lit-paths/wide-expert-parallelism)** Optimize massive models (e.g., DeepSeek-R1, GPT-OSS) using prefill/decode disaggregation and wide expert-parallelism over fast accelerator interconnects.
* **[Operational Excellence:](https://github.com/llm-d/llm-d/tree/main/docs/operations)** Ensure production stability with intelligent flow control for multi-tenant serving and proactive, SLO-aware autoscaling based on real-time inference signals.
* **[Batch Processing:](https://github.com/llm-d/llm-d/tree/main/docs/well-lit-paths/workloads/batch-serving)** Efficiently manage large-scale offline inference with OpenAI-compatible Batch APIs and asynchronous processing to maximize hardware utilization.

For a complete list of tested recipes and architectural patterns, see our [well-lit path guides](https://llm-d.ai/docs/guides). These guides provide benchmarked recipes and Helm charts to start serving quickly with best practices common to production deployments. Our intent is to eliminate the heavy lifting common in tuning and deploying generative AI inference on modern accelerators.

## Performance Highlights

Validated performance gains from production deployments and partner benchmarks:

* **3x higher output throughput** and **2x faster TTFT** with prefix-cache-aware routing vs round-robin — Llama 3.1 70B on 4× AMD MI300X, Tesla / Red Hat ([blog](https://llm-d.ai/blog/production-grade-llm-inference-at-scale-kserve-llm-d-vllm))
* **40% reduction in TTFT and ITL** with predicted-latency scheduling vs heuristics on NVIDIA GPUs, Google ([blog](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms))
* **Up to 70% higher tokens/sec** with prefill/decode disaggregation vs standard vLLM — GPT-OSS on NVIDIA B200 (p6-b200), AWS ([blog](https://aws.amazon.com/blogs/machine-learning/introducing-disaggregated-inference-on-aws-powered-by-llm-d/))
* **10–30% throughput improvement** with disaggregated serving on identical infrastructure — GPT-OSS-120B and Llama 3.3 70B on AMD MI300X, Oracle ([blog](https://blogs.oracle.com/ai-and-datascience/llm-inference-at-scale-with-llm-d-on-oci))
* **50k tokens/sec** cluster throughput with Wide Expert-Parallelism — 16×16 NVIDIA B200, ~3.1k tok/s per GPU ([blog](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale))
* **13.9x throughput improvement** with hierarchical KV offloading at 250 concurrent users vs GPU-only — 4× NVIDIA H100 ([blog](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale))

Explore detailed, reproducible benchmarks on [Prism](https://prism.llm-d.ai).

## Get Started Now

Ready to achieve SOTA performance? Follow our [Quickstart Guide](https://llm-d.ai/docs/getting-started/quickstart) to deploy your first optimized inference service on Kubernetes. You'll learn how to set up the llm-d stack, configure the intelligent router, and validate performance with production-ready benchmarks.

> [!TIP]
> Most users begin with our [Optimized Baseline](https://llm-d.ai/docs/guides/optimized-baseline), which provides a high-performance foundation for a wide range of LLM serving use cases.

## Latest News 🔥

* [2026-05] The v0.7 release introduces an optimized baseline renamed and stabilized, kustomize-first migrated guides, expanded nightly CI (OpenShift, GKE, CoreWeave), predicted-latency scheduling GA, batch gateway (experimental), and revamped project-wide documentation.
* [2026-03] llm-d [joins the CNCF as a Sandbox project](https://www.cncf.io/blog/2026/03/24/welcome-llm-d-to-the-cncf-evolving-kubernetes-into-sota-ai-infrastructure/)! Founded by Red Hat, Google Cloud, IBM Research, CoreWeave, and NVIDIA, with support from AMD, Cisco, Hugging Face, Intel, Lambda, Mistral AI, UC Berkeley, and University of Chicago. We're excited to collaborate openly on building flexible, future-proof AI infrastructure.
* [2026-02] The [v0.5](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale) introduces reproducible benchmark workflows, hierarchical KV offloading, cache-aware LoRA routing, active-active HA, UCCL-based transport resilience, and scale-to-zero autoscaling; validated ~3.1k tok/s per B200 decode GPU (wide-EP) and up to 50k output tok/s on a 16×16 B200 prefill/decode topology with order-of-magnitude TTFT reduction vs round-robin baseline.
* [2025-12] The [v0.4](https://llm-d.ai/blog/llm-d-v0.4-achieve-sota-inference-across-accelerators) release demonstrates 40% reduction in per output token latency for DeepSeek V3.1 on H200 GPUs, Intel XPU and Google TPU disaggregation support for lower time to first token, a new well-lit path for prefix cache offload to vLLM-native CPU memory tiering, and a preview of the workload variant autoscaler improving model-as-a-service efficiency.

<!-- Previous News  -->
<!-- - [2025-08] Read more about the [optimized-baseline](https://llm-d.ai/blog/intelligent-optimized-baseline-with-llm-d), including a deep dive on how different balancing techniques are composed to improve throughput without overloading replicas. -->

## 🧱 Architecture

llm-d accelerates distributed inference by integrating industry-standard open technologies like vLLM and Kubernetes. For more details, see our full [Architecture Documentation](https://llm-d.ai/docs/architecture).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Arch" src="./docs/assets/images/llm-d-arch.svg">
  </picture>
</p>

## 📦 Releases

Our [guides](./guides/README.md) are living docs and kept current. For details about the Helm charts and component releases, visit our [GitHub Releases page](https://github.com/llm-d/llm-d/releases) to review release notes.

See the [accelerator docs](./docs/getting-started/accelerators.md) for points of contact and more details about the accelerators, networks, and configurations tested.

## Contribute

We adhere to the [CNCF Code of Conduct](https://github.com/cncf/foundation/blob/main/code-of-conduct.md).

* See [our project overview](PROJECT.md) for more details on our development process and governance.
* Review [our contributing guidelines](CONTRIBUTING.md) for detailed information on how to contribute to the project.
* Join one of our [Special Interest Groups (SIGs)](SIGS.md) to contribute to specific areas of the project and collaborate with domain experts.
* We use Slack to discuss development across organizations. Please join: [Slack](https://llm-d.ai/slack)
* We host a bi-weekly standup for contributors every other Wednesday at 12:30 PM ET, as well as meetings for various SIGs. You can find them in the [shared llm-d calendar](https://red.ht/llm-d-public-calendar)
* We use Google Groups to share architecture diagrams and other content. Please join: [Google Group](https://groups.google.com/g/llm-d-contributors)

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.
