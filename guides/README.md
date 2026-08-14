# Well-Lit Path Guides

Our well-lit path guides are documented, tested, and benchmarked recipes to serve LLMs with best-practices for high performance.

> [!IMPORTANT]
> These guides are intended to be a starting point for your own configuration and deployment of model servers. Our manifests provide basic reusable building blocks for vLLM deployments and llm-d router configuration within these guides but will not support the full range of all possible configurations.

We currently offer the following:

## Intelligent Routing

* [Optimized Baseline](./optimized-baseline/README.md) - Deploy vLLM with prefix-cache and load-aware routing enabled by the llm-d EPP.
* [Predicted Latency-Based Routing](./predicted-latency-routing/README.md) - Enhance optimized baseline with real-time predictions of request latency (via a live-trained XGBoost model) rather than heuristic-based combinations of utilization metrics like queue depth or KV-cache utilization.

## Advanced KV-Cache Management

* [Precise Prefix Cache Routing](./precise-prefix-cache-routing/README.md) - Enhance optimized baseline with precise global indexing of the vLLM KV cache state.
* [Tiered Prefix Cache](./tiered-prefix-cache/README.md) - Offload KV caches beyond accelerator memory (e.g. to CPU or disk), increasing the "KV-working set size" for multi-turn inference request patterns.

## Serving Large Models

* [Prefill/Decode Disaggregation](./pd-disaggregation/README.md) - Split inference into specialized prefill and decode instances, improving throughput and quality of service stability for medium and large models like `openai/gpt-oss-120b`.
* [Wide Expert-Parallelism](./wide-ep-lws/README.md) - Deploy large Mixture-of-Experts (MoE) models like `deepseek-ai/DeepSeek-R1` over multiple nodes via DP/EP configuration, increasing available KV cache space and throughput.

## Operational Excellence

* [Flow Control](./flow-control/README.md) - Intelligent request queuing for multi-tenant deployments and managing traffic spikes.
* [Workload Autoscaling](./workload-autoscaling/README.md) - autoscale the LLM service via proactive, SLO-aware signals that reflect the true state of the inference system — queue depth, in-flight request counts, and KV cache pressure — so that capacity can be added before end-user latency is impacted.
* [Fast Model Actuation](./fast-model-actuation/README.md) - rapidly load, switch, and wake models on shared GPUs using vLLM sleep/wake and a "dual pod" technique that decouples GPU reservation from the vLLM process, avoiding cold starts.

## Workloads

Workload-centric guides — each provides the recommended, cohesive deployment for serving a workload, composing the capability guides above. See the [workload narratives](../docs/well-lit-paths/workloads/README.md) for overviews.

* [Agentic Serving](./agentic-serving/README.md) - serve long, multi-turn, tool-using agentic workloads (e.g. coding agents) by composing prefix-aware routing, KV-cache offloading, and P/D disaggregation.
* [Multimodal Serving](./multimodal-serving/README.md) - Deploy multimodal model serving (e.g., image/audio/video) using either aggregated routing or dedicated encode disaggregation topologies.
* [Reinforcement Learning](./rl/README.md) - Accelerate RL rollout by delegating rollout routing to llm-d's EPP and scheduler, bringing prefix-cache-aware routing and P/D disaggregation to RLHF/GRPO/PPO training on Ray or Slurm.

## Experimental Guides

* [Asynchronous Processing](./asynchronous-processing/README.md) - process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive batch workloads or for filling "slack" capacity in your inference pool.
* [Batch Gateway](./batch-gateway/README.md) - submit, track, and manage large-scale batch inference jobs via an OpenAI-compatible Batch API. Batch Gateway enables efficient processing of batch workloads coexisting with interactive workloads on shared infrastructure.
* [Encode Disaggregation](./multimodal-serving/e-disaggregation/README.md) - Offload multimodal encoding (images, video, audio) to dedicated workers via E/PD or E/P/D topologies, freeing prefill/decode resources for text computation.
* [Coordinator Disaggregation](./coord-disaggregation/README.md) - Drive an Encode/Prefill/Decode pipeline through a standalone Coordinator service instead of a per-pod routing sidecar, so the pipeline (which phases run, and in what order) is a configurable list of steps rather than fixed logic, and each phase's pod is picked only when that phase is about to run.

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
  - ../../../../../recipes/modelserver/components/images/gpu-vllm/release
  - ../../../../../recipes/modelserver/components/images/routing-sidecar/release
```

To change a default image for testing or a version bump, edit the component file — all guides using it pick up the change automatically.

**Nightlies:** Nightly builds are a special case — several components already ship a `nightly` variant that tracks the engine's moving `nightly` tag. Where one exists, include the `nightly` component instead of an inline override:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-sglang/nightly
```

The following components provide a `nightly` variant (run from `recipes/modelserver/components/images/`):

```console
$ tree -d -L 2 --noreport | awk 'NR==1{print} /^[├└]── /{p=$0} /nightly$/{print p; print $0}'
.
├── amd-vllm
│   ├── nightly
├── gpu-sglang
│   ├── nightly
├── routing-sidecar
│   ├── nightly
├── tpu-vllm
│   ├── nightly
└── xpu-vllm
    ├── nightly
```

**Overriding:** For any other non-default image — a vendor fork, platform variant, or a *specific* nightly tag that the component's moving `nightly` tag does not yet include — add an inline `images:` section in the overlay. The override's `name:` must match the image **as baked by the component** (registry-qualified, e.g. `docker.io/vllm/vllm-openai`), not the `REPLACE_*` placeholder — an override that names the placeholder is silently ignored. Every override **must** include a `TODO` comment with a tracking issue for cleanup:

```yaml
# TODO(#123): Remove override once upstream vLLM includes NIXL support.
images:
  - name: docker.io/vllm/vllm-openai
    newName: ghcr.io/example/custom-vllm
    newTag: nightly-20260601
```

## Authoring a Guide

Guides are two files — a machine-readable `guide.yaml` and a human-readable `README.md` whose bash code blocks are filled from the YAML by `scripts/guide.py`.

See **[`guides/templates/README.md`](./templates/README.md)** for the templates, the
quickstart, and the full authoring reference. It is the single source for those
instructions — deliberately not repeated here, so the two cannot drift apart.

## Supporting Guides

Our supporting guides address common operational challenges with model serving at scale:

* [Benchmark](../helpers/benchmark.md) demonstrates how to use automation for running benchmarks against the llm-d stack.
* [ModelExpress P2P Weight Transfer](./modelexpress-p2p/README.md) loads one model replica from storage and transfers weights to peer replicas over GPU-to-GPU RDMA for faster cold scale-outs.
