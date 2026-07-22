# Benchmarking llm-d Guides

This helper is the **single source of truth** for benchmarking a deployed llm-d stack. Individual guides (`optimized-baseline`, `pd-disaggregation`, `precise-prefix-cache-routing`, `wide-ep-lws`, `workload-autoscaling`, etc.) reference this doc for concepts, installation, configuration, and troubleshooting — and provide only the specific `llmdbenchmark` command tailored to their topology.

If you arrived here from a guide and just want the short command, skip to [Quick start](#quick-start). If you're here to understand what's actually happening, read top-to-bottom.

> [!NOTE]
> For deep customization, parameter sweeps, automatic stack creation, and recommendation tooling, see the [llm-d-benchmark project](https://github.com/llm-d/llm-d-benchmark) directly. This doc covers the run-only ("I've already deployed my stack, now benchmark it") path.

## Contents

1. [What `llmdbenchmark` does](#what-llmdbenchmark-does)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [Anatomy of an invocation](#anatomy-of-an-invocation)
5. [Resolving the endpoint](#resolving-the-endpoint)
6. [Available workload profiles](#available-workload-profiles)
7. [Supported harnesses](#supported-harnesses)
8. [HuggingFace token handling](#huggingface-token-handling)
9. [Workspace and results layout](#workspace-and-results-layout)
10. [Analysis and figures](#analysis-and-figures)
11. [Customizing the workload](#customizing-the-workload)
12. [Timeouts](#timeouts)
13. [Troubleshooting](#troubleshooting)

## What `llmdbenchmark` does

`llmdbenchmark` is the supported CLI for driving benchmarks against an llm-d stack. From the perspective of this doc — where the stack is already deployed via one of the guides — the CLI:

1. **Deploys a harness launcher pod** (`llmdbench-harness-launcher`) into your namespace using the [`llm-d-benchmark` container image](https://github.com/llm-d/llm-d-benchmark/pkgs/container/llm-d-benchmark).
2. **Renders a workload profile** (e.g. `shared_prefix_synthetic.yaml`) into a ConfigMap mounted by that pod. The profile is one of many shipped in [`workload/profiles/<harness>/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles) inside the `llm-d-benchmark` repo.
3. **Runs the chosen harness** (`inference-perf`, `guidellm`, `vllm-benchmark`, or `inferencemax`) inside the pod against the endpoint you supplied.
4. **Collects the raw harness output** locally to a workspace directory on your machine.
5. **Optionally analyzes** the collected results and generates per-request distribution plots (off by default — toggled via `--analyze`).

Everything else in the run pipeline (HF-token Secret creation, PVC handling, harness-pod lifecycle, results copy, optional cloud upload to `gs://` / `s3://`) is also handled by the CLI based on your flags.

## Installation

One line — clones the `llm-d-benchmark` repo into `./llm-d-benchmark/` and creates a virtualenv at `./llm-d-benchmark/.venv/`:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
```

Activate the venv and `cd` into the repo — **both are required**: the venv puts `llmdbenchmark` on your `PATH`, and the repo directory contains the `workload/profiles/` and `config/specification/` trees the CLI reads from at run time.

```bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```
> [!NOTE]
> A successful installation shows the following lines:
>
> ```text
> === Done ===    
> Reminder: Please activate the virtual environment in your shell:
>   source ${LLMDBENCH_VENV_DIR}/bin/activate
> To deactivate the virtual environment in your shell:
>   deactivate
> ```
> 
> An early termination of the installation could happen without clear error messages. Check if all required libraries have executable permissions (`+x`).
> [!NOTE]
> A successful installation shows the following lines:
>
> ```text
> === Done ===    
> Reminder: Please activate the virtual environment in your shell:
>   source ${LLMDBENCH_VENV_DIR}/bin/activate
> To deactivate the virtual environment in your shell:
>   deactivate
> ```
> 
> An early termination of the installation could happen without clear error messages. Check if all required libraries have executable permissions (`+x`).

> [!NOTE]
> Every subsequent `llmdbenchmark` command on this page assumes you are inside the `llm-d-benchmark` repo directory with the venv activated. If you open a new shell, re-run the two commands above.

## Quick start

The minimum useful invocation against an already-deployed stack:

```bash
llmdbenchmark \
    --spec           guides/<guide-name> \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "<exact-model-id>" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       shared_prefix_synthetic.yaml
```

The individual guide README tells you how to set `${ENDPOINT_URL}`, `${GATEWAY_CLASS}`, the spec name (e.g. `guides/optimized-baseline`), and which workload profile to start with. Everything else on this page explains why each flag is there and what else you can pass.

## Anatomy of an invocation

Every `llmdbenchmark run` against a deployed stack follows the same shape: top-level flags before `run`, run-subcommand flags after.

| Position | Flag | Purpose |
|---|---|---|
| **Top-level** | `--spec guides/<name>` | Which scenario this stack matches. Resolves to `config/specification/guides/<name>.yaml.j2` inside the repo. Required even in run-only mode because the CLI re-renders a plan to drive the harness pod. |
| Top-level | `--workspace <dir>` | (Optional) Where rendered configs, logs, and collected results land on your local machine. If omitted, an auto-generated timestamped directory is used and its path is printed in the logs at the end of the run. |
| Top-level | `--non-admin` | (Optional) Skip cluster-admin sanity checks. Use when running against a namespace where your user doesn't have cluster-wide privileges. |
| Subcommand | `run` | Selects the run subcommand. |
| `run` flag | `--endpoint-url "$URL"` | Skips auto-detection of the inference endpoint. Use the URL you resolved per the [Resolving the endpoint](#resolving-the-endpoint) section. |
| `run` flag | `--gateway-class <class>` | Tells the CLI which topology the cluster is running so its re-rendered plan matches reality. `epponly` for Standalone Mode, `istio` / `agentgateway` / `gke` etc. for Gateway Mode. |
| `run` flag | `--model "<model-id>"` | The served model name. Must match exactly what the EPP's `/v1/models` endpoint returns (e.g. `Qwen/Qwen3-32B`). |
| `run` flag | `--namespace "$NS"` | Where the harness launcher pod is deployed (typically the same namespace as the stack). |
| `run` flag | `--harness <name>` | Which harness driver to use. See [Supported harnesses](#supported-harnesses). |
| `run` flag | `--workload <profile.yaml>` | The workload profile to render and execute. See [Available workload profiles](#available-workload-profiles). |
| `run` flag | `--output local \| gs://bucket \| s3://bucket` | (Optional) Cloud upload destination on top of the local copy. Defaults to `local` (no upload). To choose a custom **local** path, use `--workspace`, not `--output`. |
| `run` flag | `--overrides "key=value,..."` | (Optional) Per-run overrides of workload-profile fields without editing the profile file. See [Customizing the workload](#customizing-the-workload). |
| `run` flag | `--analyze` | (Optional) Generate per-request distribution plots after the run. See [Analysis and figures](#analysis-and-figures). |
| `run` flag | `--wait-timeout`, `--pvc-bind-timeout`, `--data-access-timeout` | (Optional) Per-phase timeouts. See [Timeouts](#timeouts). |

## Resolving the endpoint

`llmdbenchmark run` needs an `--endpoint-url` to know where to send traffic. How you get the URL depends on whether you deployed in **Standalone Mode** (no Kubernetes Gateway resource — EPP pod with an Envoy sidecar) or **Gateway Mode**.

**Standalone Mode** (the default in most guides):

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly
```

**Gateway Mode**:

```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"
# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

The `--gateway-class` flag is important: even in run-only mode, the CLI renders a plan, and without an explicit `--gateway-class` it will not accurately detect the mode the user desires for their benchmark. Pass `--gateway-class` to make the rendered plan agree with reality and avoid surprises in downstream steps.

## Available workload profiles

All workload profiles live in the `llm-d-benchmark` repository under [`workload/profiles/<harness>/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles). When you pass `--workload <name>` and `--harness <harness>`, the CLI looks up `workload/profiles/<harness>/<name>` (and falls back to `<name>.in` to match the `.yaml.in` template files in the repo).

Always pass the `.yaml` form (e.g. `--workload shared_prefix_synthetic.yaml`), not the bare name.

Below is subset of available harnesses and workloads that are supported today - as we move forward - well-lit-paths will continue to have a tailored workload profile that highlights their strengths:

| Harness | Profile directory | Profiles shipped |
|---|---|---|
| `inference-perf` | [`workload/profiles/inference-perf/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles/inference-perf) | `shared_prefix_synthetic.yaml` (short smoke run), `shared_prefix_synthetic_short.yaml`, `shared_prefix_multi_turn_chat.yaml`, `guide_optimized-baseline_1.yaml` (reproduces the optimized-baseline report ladder), `guide_pd-disaggregation_1.yaml`, `guide_pd-disaggregation_2.yaml`, `guide_precise-prefix-cache-routing_1.yaml`, `guide_tiered-prefix-cache_1.yaml`, `guide_wide-ep-lws_1.yaml`, `chatbot_synthetic.yaml`, `chatbot_sharegpt.yaml`, `code_completion_synthetic.yaml`, `summarization_synthetic.yaml`, `agentic_code_generation.yaml`, `otel_traces.yaml`, `random_concurrent.yaml`, `sanity_random.yaml` |
| `guidellm` | [`workload/profiles/guidellm/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles/guidellm) | `shared_prefix_synthetic.yaml`, `chatbot_synthetic.yaml`, `summarization_synthetic.yaml`, `guide_optimized-baseline_1.yaml`, `guide_precise-prefix-cache-routing_1.yaml`, `guide_workload-autoscaling_1.yaml`, `sanity_concurrent.yaml`, `sanity_random.yaml` |
| `vllm-benchmark` | [`workload/profiles/vllm-benchmark/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles/vllm-benchmark) | `fixed_dataset.yaml`, `random_concurrent.yaml`, `sharegpt.yaml`, `sonnet_concurrent.yaml`, `sanity_random.yaml` |

The `guide_<guide-name>_<n>.yaml` profiles are tuned per guide and reproduce the load ladder used to generate that guide's reported numbers. Use one of these if you want to recreate published results; use `shared_prefix_synthetic.yaml` (or any `sanity_*` profile) for a smoke run.

### Authoring your own profile

Copy an existing `.yaml.in` template from the same `workload/profiles/<harness>/` directory, edit it, and pass the resulting filename (use the `.yaml` form, e.g. `--workload my_profile.yaml`) to `--workload`. The `REPLACE_ENV_*` tokens inside the template — `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL`, `REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL`, etc. — are substituted at run time from the CLI flags you supplied. No `envsubst` step is needed.

## Supported harnesses

Choose the harness whose output and metrics match your analysis needs. All four are wrapped by `llmdbenchmark` and produce harness-specific JSON in addition to a standardized cross-harness report.

| Harness | Origin | Best for |
|---|---|---|
| `inference-perf` | [kubernetes-sigs/inference-perf](https://github.com/kubernetes-sigs/inference-perf) | Multi-stage poisson / constant load ladders, shared-prefix workloads, per-request lifecycle metrics with per-stage breakdown. Default for most guides. |
| `guidellm` | [vllm-project/guidellm](https://github.com/vllm-project/guidellm) | Concurrent / constant-rate sweeps with rich latency tables and warmup/cooldown phases. Used by `workload-autoscaling`. |
| `vllm-benchmark` | [vllm-project/vllm](https://github.com/vllm-project/vllm/tree/main/benchmarks) | The reference vLLM benchmark CLI — random concurrent, ShareGPT, fixed-dataset replay. |
| `inferencemax` | [InferenceMAX](https://github.com/InferenceMAX/InferenceMAX) | Saturation-oriented harness focused on max throughput discovery. |

All harnesses capture the common metrics (TTFT, ITL, TPOT, E2E latency, throughput) but produce different report files. See [Workspace and results layout](#workspace-and-results-layout) for what each one writes to disk.

## HuggingFace token handling

For gated models (Llama, Qwen3, etc.), the harness pod needs a HuggingFace token to download the tokenizer. The token is sourced from a Kubernetes Secret named `llm-d-hf-token` in your namespace, mounted as `HF_TOKEN` and `HUGGING_FACE_HUB_TOKEN` env vars on the harness pod.

If you followed your guide's `Prerequisites` section, this Secret already exists. If not, create it now — see [`helpers/hf-token.md`](./hf-token.md) for the full reference, or this snippet:

```bash
export HF_TOKEN=<your-huggingface-token>
kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

> [!NOTE]
> As of recent development within `llm-d` all guides require a HF_TOKEN secret to be created in the deployed namespace. Even if the `model` is `non-gated`, a secret is still required.

## Workspace and results layout

Benchmark results land in the **workspace directory** on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs at the end of the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level flag (before the `run` subcommand):

```bash
llmdbenchmark --workspace <YOUR_DIR_HERE> --spec guides/<guide-name> run <remaining arguments…>
```

The on-disk layout after a successful run:

```
<workspace>/
└── runner-<timestamp>/
    ├── plan/                          # rendered scenario plan (YAML, manifests)
    ├── environment/                   # captured cluster context
    ├── results/
    |   └── <experiment-id>/           # one directory per harness invocation
    |       ├── stage_0_lifecycle_metrics.json
    |       ├── stage_1_lifecycle_metrics.json
    |       ├── …                      # one per workload stage (inference-perf)
    |       ├── summary_lifecycle_metrics.json
    |       ├── per_request_lifecycle_metrics.json
    |       ├── benchmark_report,_stage_*.yaml   # standardized cross-harness reports
    |       ├── config.yaml                       # resolved harness configuration
    |       ├── <profile-name>.yaml                # the rendered workload profile used
    |       ├── stdout.log
    |       ├── stderr.log
    └── analysis/                      # populated by --analyze
        └── <harness-id>/
            ├── latency_Vs_qps.png
            ├── throughput_vs_latency.png
            └── throughput_vs_qps.png
    ├── results/
    |   └── <experiment-id>/           # one directory per harness invocation
    |       ├── stage_0_lifecycle_metrics.json
    |       ├── stage_1_lifecycle_metrics.json
    |       ├── …                      # one per workload stage (inference-perf)
    |       ├── summary_lifecycle_metrics.json
    |       ├── per_request_lifecycle_metrics.json
    |       ├── benchmark_report,_stage_*.yaml   # standardized cross-harness reports
    |       ├── config.yaml                       # resolved harness configuration
    |       ├── <profile-name>.yaml                # the rendered workload profile used
    |       ├── stdout.log
    |       ├── stderr.log
    └── analysis/                      # populated by --analyze
        └── <harness-id>/
            ├── latency_Vs_qps.png
            ├── throughput_vs_latency.png
            └── throughput_vs_qps.png
```

The `benchmark_report,_stage_*.yaml` files use a harness-agnostic schema so you can compare runs across different harnesses. See [Benchmark Report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md) for the schema.

### Cloud upload (optional)

To mirror the local results to cloud storage as a safety net, pass `--output gs://your-bucket/prefix` or `--output s3://your-bucket/prefix`. The local copy is unaffected — the cloud upload is an additional step after collection.

**Note**: `--output` does **not** accept arbitrary local paths. Anything that isn't `local`, `gs://...`, or `s3://...` will be rejected with `Unknown output destination`. Use `--workspace` to control the local landing directory.

## Analysis and figures

Step 12 of the run pipeline (`analyze_results`) generates per-request distribution plots, but it's **off by default**. Pass `--analyze` to enable it:

```bash
llmdbenchmark \
    --spec           guides/<guide-name> \
    run \
    …other flags… \
    --analyze
```

When enabled, the analyzer reads `per_request_lifecycle_metrics.json` from each collected experiment and writes `.png` distribution plots to `runner-<timestamp>/analysis/<harness-id>/`:

| File | Content |
|---|---|
| `latency_vs_qps.png` | Inter-token-latency vs query per second |
| `throughput_vs_latency.png` | Throughput vs inter-token-latency |
| `throughput_vs_qps.png` | Throughput vs query per second|
| `latency_vs_qps.png` | Inter-token-latency vs query per second |
| `throughput_vs_latency.png` | Throughput vs inter-token-latency |
| `throughput_vs_qps.png` | Throughput vs query per second|

For more elaborate cross-run comparisons and custom plots, see the analysis notebook in the `llm-d-benchmark` repo: [`docs/analysis/analysis.ipynb`](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/analysis/analysis.ipynb).

## Customizing the workload

You have three options, in increasing order of invasiveness.

### 1. Pick a different shipped profile

The simplest path. Browse the [profile catalog](#available-workload-profiles), pick one that matches your workload shape, and pass it via `--workload`.

### 2. Author your own profile

For workloads no shipped profile matches — copy a `.yaml.in` template, edit it, and reference the resulting `.yaml` filename. See [Authoring your own profile](#authoring-your-own-profile) above.

## Timeouts

The run pipeline exposes three independent timeouts. Defaults are conservative; bump them when your cluster's StorageClass, image pull, or harness run is slower than the defaults assume.

| Flag | Default | Controls |
|---|---|---|
| `--wait-timeout` | inherited from scenario's `harness.waitTimeout` or 3600s | "Waiting for pods to start" (deploy harness) **and** "Waiting for pods to complete" (the actual benchmark run). Both phases share this budget independently. Set to `0` to dispatch and return without waiting. |
| `--data-access-timeout` | 120s | How long to wait for the data-access pod that mounts the workload PVC to become Ready. |
| `--pvc-bind-timeout` | 240s | How long to wait for the harness workload PVC to reach the `Bound` phase. Bump for slow shared-storage backends (weka, gpfs, ceph) where first-bind in a fresh namespace can take several minutes. |

Example with all three lifted for a slower environment:

```bash
llmdbenchmark \
    --spec           guides/<guide-name> \
    run \
    …other flags… \
    --wait-timeout         3600 \
    --pvc-bind-timeout     1200 \
    --data-access-timeout  600
```

All three are also exposed as env vars: `LLMDBENCH_WAIT_TIMEOUT`, `LLMDBENCH_DATA_ACCESS_TIMEOUT`, `LLMDBENCH_PVC_BIND_TIMEOUT`.

## Troubleshooting

### `did not return expected model 'X'. Available models: ['Y']`

The model name passed to `--model` doesn't match what the EPP is actually serving. Verify with:

```bash
kubectl run curl-probe --rm -it --restart=Never \
    --image=cfmanteiga/alpine-bash-curl-jq \
    -- sh -c "curl -s ${ENDPOINT_URL}/v1/models | grep -o '\"id\":\"[^\"]*\"'"
```

Pass the exact ID it prints to `--model`.

### `Unknown output destination: ./results`

You passed a local filesystem path to `--output`. `--output` only accepts `local`, `gs://...`, or `s3://...`. Drop the flag (results still land locally in the workspace) or use `--workspace /your/path` to choose a custom local directory.

### `Timed out after 240s waiting for workload PVC`

Your cluster's StorageClass takes longer than 240s to bind a fresh PVC. Pass `--pvc-bind-timeout 1200` (or longer) on the `run` subcommand. Worth verifying the StorageClass behavior with `kubectl get sc` and `kubectl describe pvc` to confirm it's not a deeper issue (no default StorageClass, quota exhausted, etc.).

### `Could not detect endpoint for <stack>`

You didn't pass `--endpoint-url`, and the run-phase's auto-detection couldn't find the service. In run-only mode against an already-deployed stack, always pass `--endpoint-url` (and `--gateway-class`, see [Resolving the endpoint](#resolving-the-endpoint)).

### `HF_TOKEN is not set and Secret 'llm-d-hf-token' does not exist`

Either export `HF_TOKEN` in your shell before running, or create the Secret in the namespace by hand — see [`helpers/hf-token.md`](./hf-token.md).

### Harness pod hangs at "ContainerCreating" or "ImagePullBackOff"

Image pull is slow on the first run of the day. Bump `--wait-timeout 1800` to give the kubelet time. If the pull is failing entirely, check the pod's events with `kubectl describe pod -l app=llmdbench-harness-launcher` — common causes are missing image-pull secrets on the namespace or a private registry without credentials.

### `unrecognized arguments: --pvc-bind-timeout` (or similar)

Your installed `llmdbenchmark` is older than the flag was added. Re-run the [install one-liner](#installation) to refresh the clone from `main`, or check the version with `llmdbenchmark --version`. CI environments that pin to a specific tag may need to be bumped to a release that includes the flag.

### Run completes but `analysis/distributions/` is empty (or missing)

You didn't pass `--analyze`. Add the flag and re-run, or run analysis separately by pointing `llm-d-benchmark`'s analysis tooling at the existing `per_request_lifecycle_metrics.json`.

---

For everything beyond this — DoE experiments with full stack standup/teardown cycles, custom harness images, multi-cluster runs, recommendations — see the [llm-d-benchmark repository](https://github.com/llm-d/llm-d-benchmark) directly.
