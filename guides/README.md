# Well-Lit Path Guides

Our well-lit path guides are documented, tested, and benchmarked recipes to serve LLMs with best-practices for high performance.

> [!IMPORTANT]
> These guides are intended to be a starting point for your own configuration and deployment of model servers. Our manifests provide basic reusable building blocks for vLLM deployments and llm-d router configuration within these guides but will not support the full range of all possible configurations.

We currently offer the following:

### Intelligent Routing

* [Optimized Baseline](./optimized-baseline/README.md) - Deploy vLLM with prefix-cache and load-aware routing enabled by the llm-d EPP.
* [Predicted Latency-Based Routing](./predicted-latency-routing/README.md) - Enhance optimized baseline with real-time predictions of request latency (via a live-trained XGBoost model) rather than heuristic-based combinations of utilization metrics like queue depth or KV-cache utilization.

### Advanced KV-Cache Management

* [Precise Prefix Cache Routing](./precise-prefix-cache-routing/README.md) - Enhance optimized baseline with precise global indexing of the vLLM KV cache state.
* [Tiered Prefix Cache](./tiered-prefix-cache/README.md) - Offload KV caches beyond accelerator memory (e.g. to CPU or disk), increasing the "KV-working set size" for multi-turn inference request patterns.

### Serving Large Models

* [Prefill/Decode Disaggregation](./pd-disaggregation/README.md) - Split inference into specialized prefill and decode instances, improving throughput and quality of service stability for medium and large models like `openai/gpt-oss-120b`.
* [Wide Expert-Parallelism](./wide-ep-lws/README.md) - Deploy large Mixture-of-Experts (MoE) models like `deepseek-ai/DeepSeek-R1` over multiple nodes via DP/EP configuration, increasing available KV cache space and throughput.

### Operational Excellence

* [Flow Control](./flow-control/README.md) - Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
* [Workload Autoscaling](./workload-autoscaling/README.md) - autoscale the LLM service via proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.
* [Rollouts](./rollouts/README.md) - perform incremental rollout operations for LoRA adapters, base models, and model server versions with minimal service disruption using traffic splitting and gradual deployment strategies.

### Workloads

Workload-centric guides — each provides the recommended, cohesive deployment for serving a workload, composing the capability guides above. See the [workload narratives](../docs/well-lit-paths/workloads/README.md) for overviews.

* [Agentic Serving](./agentic-serving/README.md) - serve long, multi-turn, tool-using agentic workloads (e.g. coding agents) by composing prefix-aware routing, KV-cache offloading, and P/D disaggregation.
* [Multimodal Serving](./multimodal-serving/README.md) - Deploy multimodal model serving (e.g., image/audio/video) using either aggregated routing or dedicated encode disaggregation topologies.

## Experimental Guides

* [Asynchronous Processing](./asynchronous-processing/README.md) - process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive batch workloads or for filling "slack" capacity in your inference pool.
* [Batch Gateway](./batch-gateway/README.md) - submit, track, and manage large-scale batch inference jobs via an OpenAI-compatible Batch API. Batch Gateway enables efficient processing of batch workloads coexisting with interactive workloads on shared infrastructure.
* [Encode Disaggregation](./multimodal-serving/e-disaggregation/README.md) - Offload multimodal encoding (images, video, audio) to dedicated workers via E/PD or E/P/D topologies, freeing prefill/decode resources for text computation.

## Centralized Configuration

### Shared Environment Variables (`env.sh`)

[`guides/env.sh`](./env.sh) defines shared environment variables used across all guides. Source it before running guide commands:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

See [`env.sh`](./env.sh) for the full list of variables it provides (Helm chart versions, chart OCI URLs, etc.).

### Default Container Images

Default model server and sidecar images are defined as [Kustomize Components](./recipes/modelserver/components/images/README.md) under `recipes/modelserver/components/images/`. Guides include the relevant component instead of hardcoding image versions:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm
  - ../../../../../recipes/modelserver/components/images/routing-sidecar
```

To change a default image for testing or a version bump, edit the component file — all guides using it pick up the change automatically.

**Overriding:** If a guide requires a non-default image (nightly build, vendor fork, platform variant), add an inline `images:` section in the overlay. Every override **must** include a `TODO` comment with a tracking issue for cleanup:

```yaml
# TODO(#123): Remove override once upstream vLLM includes NIXL support.
images:
  - name: REPLACE_MODEL_SERVER_IMAGE
    newName: ghcr.io/example/custom-vllm
    newTag: nightly-20260601
```

## Supporting Guides

Our supporting guides address common operational challenges with model serving at scale:

* [Benchmark](../helpers/benchmark.md) demonstrates how to use automation for running benchmarks against the llm-d stack.
