# Well-Lit Path Guides

Our well-lit path guides are documented, tested, and benchmarked recipes to serve LLMs with best-practices for high performance.

> [!IMPORTANT]
> These guides are intended to be a starting point for your own configuration and deployment of model servers. Our manifests provide basic reusable building blocks for vLLM deployments and llm-d router configuration within these guides but will not support the full range of all possible configurations.

We currently offer the following:

### Intelligent Routing

* [Optimized Baseline](./optimized-baseline/README.md) - Deploy vLLM with prefix-cache and load-aware routing enabled by the llm-d EPP.
* [Predicted Latency-Based Routing](./predicted-latency-based-scheduling/README.md) - Enhance optimized baseline with real-time predictions of request latency (via a live-trained XGBoost model) rather than heuristic-based combinations of utilization metrics like queue depth or KV-cache utilization.

### Advanced KV-Cache Management

* [Precise Prefix Cache Routing](./precise-prefix-cache-aware/README.md) - Enhance optimized baseline with precise global indexing of the vLLM KV cache state.
* [Tiered Prefix Cache](./tiered-prefix-cache/README.md) - Offload KV caches beyond accelerator memory (e.g. to CPU or disk), increasing the "KV-working set size" for multi-turn inference request patterns.

### Serving Large Models

* [Prefill/Decode Disaggregation](./pd-disaggregation/README.md) - Split inference into specialized prefill and decode instances, improving throughput and quality of service stability for medium and large models like `openai/gpt-oss-120b`.
* [Wide Expert-Parallelism](./wide-ep-lws/README.md) - Deploy large Mixture-of-Experts (MoE) models like `deepseek-ai/DeepSeek-R1` over multiple nodes via DP/EP configuration, increasing available KV cache space and throughput.

### Operational Excellence

* [Flow Control](./flow-control.md): Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
* [Workload Autoscaling](./workload-autoscaling/README.md) - autoscale the LLM service via proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.

## Experimental Guides

* [Asynchronous Processing](./asynchronous-processing/README.md) - process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive batch workloads or for filling "slack" capacity in your inference pool.
* [Batch Gateway](./batch-gateway/README.md) - submit, track, and manage large-scale batch inference jobs via an OpenAI-compatible Batch API. Batch Gateway enables efficient processing of batch workloads coexisting with interactive workloads on shared infrastructure.

## Supporting Guides

Our supporting guides address common operational challenges with model serving at scale:

* [Benchmark](../helpers/benchmark.md) demonstrates how to use automation for running benchmarks against the llm-d stack.

## Nightly Testing

Auto-updated from GitHub Actions:

| Guide | OCP | CKS | GKE |
|-------|-----|-----|-----|
| [Optimized Baseline](./optimized-baseline/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml) |
| [Precise Prefix Cache Aware Routing](./precise-prefix-cache-aware/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml) |
| [P/D Disaggregation](./pd-disaggregation/README.md) | NOT TESTED IN CI | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml) |
| [Wide Expert Parallelism](./wide-ep-lws/README.md) | NOT TESTED IN CI | NOT TESTED IN CI | NOT TESTED IN CI |
| [Tiered Prefix Cache](./tiered-prefix-cache/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-ocp.yaml) | NOT TESTED IN CI | NOT TESTED IN CI |
| [Predicte Latency-Based Routing](./predicted-latency-based-scheduling/README.md) | NOT TESTED IN CI | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-predicted-latency-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-predicted-latency-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-predicted-latency-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-predicted-latency-gke.yaml)  |
| [Workload Autoscaling (WVA)](./workload-autoscaling/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml) | NOT TESTED IN CI |

<!--
| Guide | OCP | CKS | GKE |
|-------|-----|-----|-----|
| [Optimized Baseline](./optimized-baseline/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml) |
| [Precise Prefix Cache Aware Routing](./precise-prefix-cache-aware/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-precise-prefix-cache-gke.yaml) |
| [P/D Disaggregation](./pd-disaggregation/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml) |
| [Wide Expert Parallelism](./wide-ep-lws/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml) | [![GKE](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml) |
| [Tiered Prefix Cache](./tiered-prefix-cache/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-tiered-prefix-cache-ocp.yaml) | | |
| [Workload Autoscaling (WVA)](./workload-autoscaling/README.md) | [![OCP](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-ocp.yaml) | [![CKS](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wva-cks.yaml) | |
-->
