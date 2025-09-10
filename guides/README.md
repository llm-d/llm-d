# Deploying distributed inference on Kubernetes with llm-d

Our guides provide tested and benchmarked recipes and Helm charts to start serving large language models (LLMs) quickly with best practices common to production deployments. A familiarity with basic deployment and operation of Kubernetes is assumed.

If you want to learn by doing, follow a [first deployment quick start](./QUICKSTART.md).

These guides are intended to be used as a starting point for your own configuration and deployment of model servers. Our Helm charts provide basic reusable building blocks for vLLM deployments and inference scheduler configuration within these guides but will not support the full range of all possible configurations. Both guides and charts depend on features provided and supported in the [vLLM](https://github.com/vllm-project/vllm) and [inference gateway](https://github.com/kubernetes-sigs/gateway-api-inference-extension) open source projects.

## Who are these guides (and llm-d) for?

These guides are targeted to startups and enterprises to deploy production LLM serving at scale and minimize long term maintenance. We focus on the following use cases:

* Deploying a self-hosted LLM behind a single workload across tens or hundreds of nodes
* Running a production model-as-a-service platform that supports many users and workloads sharing one or more LLM deployments

## Well-Lit Path Guides

A well-lit path is a documented, tested, and benchmarked solution of choice to reduce risk and maintenance cost. These are the central best practices common to production deployments of large language model serving.

We currently offer three tested and benchmarked paths to help you deploy large models:

1. [Intelligent Inference Scheduling](./inference-scheduling) - Deploy [vLLM](https://docs.vllm.ai) behind the [Inference Gateway (IGW)](https://github.com/kubernetes-sigs/gateway-api-inference-extension) to decrease latency and increase throughput via [precise prefix-cache aware routing](./precise-prefix-cache-aware/) and [customizable scheduling policies](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md).
2. [Prefill/Decode Disaggregation](./pd-disaggregation) - Reduce time to first token (TTFT) and get more predictable time per output token (TPOT) by splitting inference into prefill servers handling prompts and decode servers handling responses, primarily on large models such as Llama-70B and when processing very long prompts.
3. [Wide Expert-Parallelism](./wide-ep-lws) - Deploy very large Mixture-of-Experts (MoE) models like [DeepSeek-R1](https://huggingface.co/deepseek-ai/DeepSeek-R1) and significantly reduce end-to-end latency and increase throughput by scaling up with [Data Parallelism and Expert Parallelism](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment.html) over fast accelerator networks.

## Supporting Guides

Our supporting guides address common operational challenges with model serving at scale:

- [Simulating model servers](./simulated-accelerators) can deploy a vLLM model server simulator that allows testing inference scheduling and orchestration at scale as each instance does not need accelerators.

## Other Guides

The following guides have been provided by the community but do not fully integrate into the llm-d configuration structure yet and are not fully supported as well-lit paths:

* TBD

Note: New guides added to this list must enable at least one of the core well-lit paths but may directly include prerequisite steps specific to new hardware or infrastructure providers without full abstraction. A guide added here is expected to eventually become path of an existing well-lit path.