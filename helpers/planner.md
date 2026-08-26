# Planning an llm-d Deployment

[llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner) helps you narrow down a particular `llm-d` configuration needed to support your usage requirements by using a data driven, AI infused methodology.

You provide the `planner` with key details for your workload and it searches benchmark data for the model and accelerator combinations that can meet them. The `planner` will then generate deployment artifacts and manifests for the option you choose.

There are a variety of ways to interact with the `planner`, some feature direct UI experience and others feature embedding it's API within your existing library (`llm-d-benchmark` leverages the `Capacity Planner` option today!).

## Contents

1. [What you get](#what-you-get)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [Use cases, traffic profiles, and SLOs](#use-cases-traffic-profiles-and-slos)
5. [Where the numbers come from](#where-the-numbers-come-from)
6. [Deploying onto llm-d](#deploying-onto-llm-d)
7. [Bringing your own benchmark data](#bringing-your-own-benchmark-data)
8. [Good to know](#good-to-know)

## What you get

The project bundles three tools. The `Planner` is the main workflow and calls the other 2 workflows if benchmark data runs scarce, but both of those are useful in their own right, and each one has a UI page, a CLI command, and REST endpoints.

| Tool | Answers | Interface |
| --- | --- | --- |
| **Planner** | Given this workload and these SLOs, what should I deploy, and what will it cost? | UI main page, REST, Python library |
| **Capacity Planner** | Does this model fit? Memory for weights, KV cache (MHA, GQA, MQA, MLA), activations, and overhead, along with minimum accelerator count, maximum context length, and concurrent request capacity. | UI page, `planner plan`, REST |
| **GPU Recommender** | Which accelerator should I ask for? Estimated TTFT, ITL, and throughput per accelerator type, with a cost comparison, without running a benchmark. | UI page, `planner estimate`, REST |

## Installation

How much you install depends on how much of the tool you need. If you only want the Capacity Planner, the GPU Recommender, or the recommendation pipeline as a library, the wheel is enough:

```bash
pip install llm-d-planner
```

That core install runs without a server, an LLM, or a cluster behind it.

Optional extras cover the rest: `[estimation]` adds HuggingFace model config lookups and roofline estimates, `[llm]`, `[openai]`, and `[vertex]` each add natural-language intent extraction through that provider, `[server]` adds the REST API, `[kubernetes]` lets the library apply a generated bundle, and `[ui]` adds the Streamlit interface.

> [!NOTE]
> `planner estimate` also needs BentoML's roofline model, which is not published to PyPI:
>
> ```bash
> pip install "llm-optimizer @ git+https://github.com/bentoml/llm-optimizer.git"
> ```

The upstream [README prerequisites](https://github.com/llm-d-incubation/llm-d-planner/blob/main/README.md#prerequisites) and [Developer Guide](https://github.com/llm-d-incubation/llm-d-planner/blob/main/docs/DEVELOPER_GUIDE.md) carry the current list and the platform notes that go with it. Also see [Deployment Guide](https://github.com/llm-d-incubation/llm-d-planner/blob/main/docs/DEPLOYMENT_GUIDE.md) covers hosting a single shared instance for a team.

## Quick start

First clone the repo:

```bash
git clone git@github.com:llm-d-incubation/llm-d-planner.git
```

## UI Mode

```bash
make setup          # install dependencies, pull the Ollama model
make start          # start the database, Ollama, backend, and UI
make db-load-blis   # load the bundled BLIS benchmark data
```

Open `http://localhost:8501` and the main page walks you through the workflow. Most of it is clicking forward; steps 4 and 6 are where your own judgement comes in.

1. **Describe your use case.** For example: "I need a chatbot for 5000 users with low latency" is enough, and a local LLM reads structured intent out of it. If you already know exactly what you want, a form mode lets you skip the LLM and fill the fields in directly.
2. **Analyze use case.** This produces the intent: use case, user count, priorities, and any accelerator or model preferences you mentioned.
3. **Generate specification.** The intent becomes a traffic profile and a set of SLO targets.
4. **Review the specification.** Everything the previous step derived is editable and shows its recommended range beside it, including the TTFT, ITL, and E2E targets, the percentile they apply at, the expected RPS, and the weights used for scoring.
5. **Generate recommendations.** The Planner searches its benchmark data for configurations that meet those targets.
6. **Select a recommendation.** You get four ranked views of the same search, sorted for balance, quality, cost, and latency, and you choose which trade-off you want.
7. **Deploy.** The Deployment tab lets you review, copy, or download the generated manifests, or apply them to a connected cluster.

When you are done, `make stop` leaves Ollama and the database running, and `make stop-all` shuts everything down.

## Standalone (Headless) Mode

For headless mode, you only need to have the `wheel` installed.

```bash
pip install llm-d-planner
```

Below are a couple of examples of directly invoking the library using the CLI:

```bash
planner plan --model Qwen/Qwen3-32B --gpu-memory 80 --tp 4
```

```bash
planner estimate --model Qwen/Qwen3-32B --input-len 512 --output-len 128 \
    --gpu-list H100,A100,L40 --max-ttft 100 --max-itl 10 --pretty
```

A few flags are worth knowing early:

- `plan`, `--max-model-len -1` calculates the longest context that fits in the memory you gave it.

- `--show-possible-tp` lists the tensor parallelism values the model actually supports.

- `estimate`, `--custom-gpu-cost H100:30.50` re-ranks everything against your own pricing rather than the defaults. Both accept `--output results.json` when you want to feed the results to something else.

- `--help` covers the rest.

## Library Mode

The same pipeline found from the `cli` is available as a library - `llmdbenchmark` particularly uses the CapacityPlanner aspect - which lets a script work out whether a model and GPU configuration will actually fit before anything is deployed:

```python
from planner.capacity_planner import (
    KVCacheDetail,
    allocatable_kv_cache_memory,
    get_model_config_from_hf,
    max_concurrent_requests,
    model_memory_req,
)

MODEL = "Qwen/Qwen3-32B"
GPU_MEMORY = 80  # GB per accelerator
TP = 4
MAX_MODEL_LEN = 32768

config = get_model_config_from_hf(MODEL)  # pass hf_token=... for gated models

weights_gb = model_memory_req(MODEL, config)
kv_cache_gb = allocatable_kv_cache_memory(
    MODEL, config, GPU_MEMORY, tp=TP, max_model_len=MAX_MODEL_LEN
)
per_request_gb = KVCacheDetail(MODEL, config, MAX_MODEL_LEN).per_request_kv_cache_gb
concurrent = max_concurrent_requests(MODEL, config, MAX_MODEL_LEN, GPU_MEMORY, tp=TP)

print(f"weights:       {weights_gb:.1f} GB")
print(f"KV cache left: {kv_cache_gb:.1f} GB across {TP} GPUs")
print(f"per request:   {per_request_gb:.2f} GB at {MAX_MODEL_LEN} tokens")
print(f"headroom for {concurrent} concurrent requests")
```

These functions need the `[estimation]` extra, since they read the model config from HuggingFace. `allocatable_kv_cache_memory()` returns a negative number when the weights and activation memory do not leave room for any KV cache at all, which is how `llmdbenchmark` catches a deployment that would fail before it launches it. The full recommendation pipeline is a library too, through the `Planner` class, and both it and the matching REST endpoints are documented in the upstream [Programmatic API User Guide](https://github.com/llm-d-incubation/llm-d-planner/blob/main/docs/PROGRAMMATIC_API_USER_GUIDE.md).

## Use cases, traffic profiles, and SLOs

The Planner does not reason about an arbitrary workload description from scratch. It recognizes nine use cases, and each one carries a traffic profile describing the token shape of the workload and an experience class describing how fast the interaction has to feel. Those two together produce the SLO targets you are asked to approve.

| Use case | Traffic profile (prompt → output) | Experience class | TTFT p95 | ITL p95 | E2E p95 |
| --- | --- | --- | --- | --- | --- |
| `chatbot_conversational` | 512 → 256 | Conversational | ≤150 ms | ≤25 ms | ≤7 s |
| `code_completion` | 512 → 256 | Instant | ≤100 ms | ≤20 ms | ≤5 s |
| `code_generation_detailed` | 1024 → 1024 | Interactive | ≤300 ms | ≤30 ms | ≤25 s |
| `translation` | 1024 → 1024 | Deferred | ≤400 ms | ≤35 ms | ≤35 s |
| `content_generation` | 1024 → 1024 | Deferred / batch | ≤500 ms | ≤35 ms | ≤40 s |
| `summarization_short` | 4096 → 512 | Interactive / deferred | ≤600 ms | ≤30 ms | ≤15–20 s |
| `document_analysis_rag` | 4096 → 512 | Interactive | ≤600 ms | ≤30 ms | ≤18 s |
| `long_document_summarization` | 10240 → 1536 | Deferred | ≤1 s | ≤40 ms | ≤60 s |
| `research_legal_analysis` | 10240 → 1536 | Batch / offline | ≤2 s | ≤45 ms | ≤75–90 s |

The split between the two columns matters because prompt tokens and output tokens stress different parts of inference. Prompt length drives prefill, which surfaces as time to first token, while output length drives generation, which surfaces as inter-token and end-to-end latency.

Pick the use case closest to your workload, then adjust the numbers during the specification review. The upstream [Traffic and SLOs](https://github.com/llm-d-incubation/llm-d-planner/blob/main/docs/traffic_and_slos.md) doc lays out the whole framework, including all five experience classes and the reasoning behind each mapping.

## Where the numbers come from

Three different kinds of input feed a recommendation, and it helps to know which one you are reading.

Benchmarks come first, matched exactly on model, accelerator, and traffic profile. The repo bundles BLIS data to get you started, and you can load results, interpolated data, and estimated data on top of it.

Where no benchmark exists for a given pair, the Planner estimates performance analytically using BentoML's [llm-optimizer](https://github.com/bentoml/llm-optimizer) roofline model, so the search is not confined to combinations somebody has already measured. The UI labels those results Estimated rather than Benchmarked, and they are best read as a shortlist worth measuring rather than as an answer.

Quality scores come from public evaluations instead of from your cluster: human preference ratings from [Arena](https://lmarena.ai/) and automated benchmark results from [Artificial Analysis](https://artificialanalysis.ai/), composited per use case so that a code completion workload leans on coding benchmarks more heavily than a chat workload does. The upstream [Quality Scoring Guide](https://github.com/llm-d-incubation/llm-d-planner/blob/main/docs/QUALITY_SCORING_GUIDE.md) explains how the two sources are normalized against each other.

## Deploying onto llm-d

Left alone, the Planner writes a vLLM deployment. To target the stack these guides deploy, choose the llm-d option through the "llm-d (inference stack)" radio on the Deployment tab, `"stack": "llm-d"` on `POST /api/v1/generate-deployment`, or `stack="llm-d"` in the library. What comes out is a kustomize overlay over this repo's [model server recipe](../guides/recipes/modelserver/base/single-host/default) together with Helm values for the scheduler:

```text
generated_configs/<deployment-id>/
├── modelserver/
│   ├── kustomization.yaml   # overlay on guides/recipes/modelserver/base/single-host/default
│   └── patch-vllm.yaml      # vllm serve args, tensor parallelism, accelerator requests, replicas
└── scheduler/
    └── values.yaml          # InferencePool and endpoint picker (EPP) values
```

Read through what it generated, then apply the model servers and install the scheduler:

```bash
kubectl apply -k generated_configs/<deployment-id>/modelserver -n ${NAMESPACE}
```

```bash
helm install <deployment-id> \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f generated_configs/<deployment-id>/scheduler/values.yaml \
    -n ${NAMESPACE}
```

Once it is running, [`helpers/smoke-test/`](./smoke-test/README.md) confirms the endpoint is serving and [`helpers/benchmark.md`](./benchmark.md) tells you how it behaves under load.

> [!IMPORTANT]
> Treat the generated manifests as a starting point rather than a substitute for a well-lit path.
>
> In practice you will get further by deploying whichever guide matches your topology, such as [Optimized Baseline](../guides/optimized-baseline/README.md) or [Prefill/Decode Disaggregation](../guides/pd-disaggregation/README.md), and carrying the Planner's model, parallelism, and replica numbers into it.

## Bringing your own benchmark data

A recommendation is only as good as the benchmark data behind it, and the bundled data will not cover every combination of accelerator, model, and traffic profile that matters to you. Once you have a stack deployed, run the workload you care about with [`helpers/benchmark.md`](./benchmark.md) and load the results back into the Planner. The `make db-load-*` targets append to the database, the UI uploads them from its Configuration tab, and `POST /api/v1/db/upload-benchmarks` does the same over REST. From then on, recommendations for that combination rest on measurements from your own cluster.

## Good to know

Gated models need a token. The Capacity Planner reads model configuration directly from HuggingFace, so sizing a Llama model means exporting `HF_TOKEN` into the environment the backend runs in. It is the same token the guides use for the `llm-d-hf-token` Secret, described in [`helpers/hf-token.md`](./hf-token.md), and the backend always reads it from its own environment rather than from the request.

The backend binds port 8000, which is also the port the guides use when port-forwarding to the gateway, so stop one before you start the other.

---

For anything past this page, including the architecture, the internals of the scoring, and how to contribute, go to the [llm-d-planner repository](https://github.com/llm-d-incubation/llm-d-planner) directly.
