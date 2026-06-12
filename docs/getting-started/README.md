# The fastest path to state-of-the-art LLM inference on any accelerator

llm-d is an open-source inference serving stack for Kubernetes. It runs your model server of choice—vLLM and SGLang, and more—across your cluster, turning single-node engines into production-grade distributed inference on the infrastructure you already run. Get state-of-the-art performance for leading open models—on NVIDIA, AMD, and custom accelerators.

Built for agentic pipelines, LLMs, multimodal models, and high-throughput serving. Completely engine- and hardware-agnostic.

<div class="intro-founded">
  <span>llm-d is founded by:</span>
  <div class="intro-founded-logos">
    <img class="intro-founder-logo" src="/img/logos/IBM.png" alt="IBM" height="18" />
    <img class="intro-founder-logo" src="/img/logos/Redhat.png" alt="Red Hat" height="18" />
    <img class="intro-founder-logo" src="/img/logos/Google.png" alt="Google Cloud" height="18" />
    <img class="intro-founder-logo" src="/img/logos/Nvidia.png" alt="NVIDIA" height="18" />
    <img class="intro-founder-logo" src="/img/logos/Coreweave.png" alt="CoreWeave" height="18" />
  </div>
</div>

<div class="intro-cncf">
  <img class="intro-cncf-logo" src="/img/CNCF-logo.svg" alt="CNCF" />
  llm-d is a CNCF Sandbox project
</div>

<div class="intro-ctas">
  <a class="button button--primary" href="./quickstart">Get started</a>
  <a class="button button--secondary" href="/docs/guides/optimized-baseline">View performance analysis</a>
</div>

## Key capabilities

- [**Optimized Baseline**](/docs/guides/optimized-baseline) — Standard load balancing treats every replica the same. LLM workloads don't work that way. llm-d routes each request to the replica best positioned to serve it, using prefix-cache awareness, load, and predicted latency.
- [**Advanced KV-cache management**](/docs/guides/kv-cache-management) — Multi-turn conversations force clusters to redo work they've already done. Tiered offloading to CPU and disk, with global indexing of cache state, keeps that work available instead of recomputing it. That reuse is what makes agent pipelines and long multi-turn sessions affordable to serve.
- [**Serving large models**](/docs/guides/serving-large-models) — llm-d makes massive models like DeepSeek-R1 and GPT-OSS practical to serve, using prefill/decode disaggregation and wide expert-parallelism over fast accelerator interconnects.
- [**Operational excellence**](/docs/guides/operational-excellence) — Production traffic is messy. Intelligent flow control keeps multi-tenant serving stable, and SLO-aware autoscaling reacts to real-time inference signals rather than generic infrastructure metrics.
- [**Batch processing (experimental)**](/docs/guides/batch-processing) — Large-scale offline inference through OpenAI-compatible Batch APIs, processed asynchronously to keep hardware utilization high.

<div class="intro-ctas">
  <a class="button button--primary" href="/docs/guides">See all guides</a>
</div>

## Why use llm-d?

llm-d's performance numbers come from production deployments and partner benchmarks, not projections.

- 3× higher throughput, 2× faster time to first token with cache-aware routing. Tesla and Red Hat, on AMD MI300X.
- Up to 70% more tokens per second with disaggregated serving. AWS, on NVIDIA B200.
- 40% lower time to first token and inter-token latency with predicted-latency scheduling. Google, on NVIDIA GPUs.
- 13.9× throughput at 250 concurrent users with hierarchical KV offloading. On NVIDIA H100.

Oracle measured 10–30% gains from disaggregation on identical infrastructure, and wide expert-parallelism sustained 50,000 tokens per second across a 16×16 B200 cluster. Every benchmark is reproducible: configurations, workflows, and full results are published on [Prism](https://prism.llm-d.ai).
