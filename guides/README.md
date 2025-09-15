# High performance distributed inference on Kubernetes with llm-d

Our guides provide tested and benchmarked recipes and Helm charts to serve large language models (LLMs) at peak performance with best practices common to production deployments. A familiarity with basic deployment and operation of Kubernetes is assumed.

> [!TIP]
> If you want to learn by doing, follow a [step-by-step first deployment with QUICKSTART.md](./QUICKSTART.md).

## Who are these guides (and llm-d) for?

These guides are targeted at startups and enterprises deploying production LLM serving that want the best possible performance while minimizing operational complexity. State of the art LLM inference involves multiple optimizations that offer meaningful tradeoffs, depending on use case. The guides help identify those key optimizations, understand their tradeoffs, and verify the gains against your own workload.

We focus on the following use cases:

* Deploying a self-hosted LLM behind a single workload across tens or hundreds of nodes
* Running a production model-as-a-service platform that supports many users and workloads sharing one or more LLM deployments

## Well-Lit Path Guides

A well-lit path is a documented, tested, and benchmarked solution of choice to reduce adoption risk and maintenance cost. These are the central best practices common to production deployments of large language model serving.

We currently offer three tested and benchmarked paths to help you deploy large models:

1. [Intelligent Inference Scheduling](./inference-scheduling/README.md) - Deploy [vLLM](https://docs.vllm.ai) behind the [Inference Gateway (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension) to decrease latency and increase throughput via [precise prefix-cache aware routing](./precise-prefix-cache-aware/README.md) and [customizable scheduling policies](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md).
2. [Prefill/Decode Disaggregation](./pd-disaggregation/README.md) - Reduce time to first token (TTFT) and get more predictable time per output token (TPOT) by splitting inference into prefill servers handling prompts and decode servers handling responses, primarily on large models such as Llama-70B and when processing very long prompts.
3. [Wide Expert-Parallelism](./wide-ep-lws/README.md) - Deploy very large Mixture-of-Experts (MoE) models like [DeepSeek-R1](https://huggingface.co/deepseek-ai/DeepSeek-R1) and significantly reduce end-to-end latency and increase throughput by scaling up with [Data Parallelism and Expert Parallelism](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment.html) over fast accelerator networks.

> [!IMPORTANT]
> These guides are intended to be a starting point for your own configuration and deployment of model servers. Our Helm charts provide basic reusable building blocks for vLLM deployments and inference scheduler configuration within these guides but will not support the full range of all possible configurations. Both guides and charts depend on features provided and supported in the [vLLM](https://github.com/vllm-project/vllm) and [inference gateway](https://github.com/kubernetes-sigs/gateway-api-inference-extension) open source projects.

## Supporting Guides

Our supporting guides address common operational challenges with model serving at scale:

- [Simulating model servers](./simulated-accelerators/README.md) can deploy a vLLM model server simulator that allows testing inference scheduling and orchestration at scale as each instance does not need accelerators.

## Other Guides

The following guides have been provided by the community but do not fully integrate into the llm-d configuration structure yet and are not fully supported as well-lit paths:

* Coming Soon!

> [!NOTE]
> New guides added to this list enable at least one of the core well-lit paths but may directly include prerequisite steps specific to new hardware or infrastructure providers without full abstraction. A guide added here is expected to eventually become path of an existing well-lit path.