# Performance assessment

This document is the main reference for users aiming at benchmarking the deployed llm-d stacks. Individual guides (e.g., `optimized-baseline`, `pd-disaggregation`, `precise-prefix-cache-routing` and `wide-ep`) rely on this doc for all operations and procedures related to performance assessment via systematic benchmarking.

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
10. [Analysis and performance data visualization](#analysis-and-performance-data-visualization)
11. [Customizing the workload](#customizing-the-workload)
12. [Timeouts](#timeouts)
13. [Cleaning up benchmark resources](#cleaning-up-benchmark-resources)
14. [Troubleshooting](#troubleshooting)

## What `llmdbenchmark` does

`llmdbenchmark` is the supported CLI for driving benchmarks against an llm-d stack. From the perspective of this documentation — where the stack is already deployed via one of the guides — the CLI:

1. **Deploys a harness launcher pod** (`llmdbench-harness-launcher`) into your namespace using the [`llm-d-benchmark` container image](https://github.com/llm-d/llm-d-benchmark/pkgs/container/llm-d-benchmark).
2. **Renders a workload profile** (e.g. `sanity_random.yaml`) into a ConfigMap mounted by that pod. The profile is one of many shipped in [`workload/profiles/<harness>/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles) inside the `llm-d-benchmark` repo.
3. **Runs the chosen harness** (e.g., `aiperf`, `guidellm`, `inferencemax`, `inference-perf`, `lm-eval` and `vllm-benchmark`) inside the pod against the endpoint you supplied.
4. **Collects the raw harness output** locally to a workspace directory on your machine.
5. **Deploys [llm-d-prism](https://github.com/llm-d/llm-d-prism)** for performance data visualization (disable with `--no-prism`; also skipped with `--no-pvc`).

Everything else in the run pipeline (copying the HF-token Secret into the harness namespace, PVC handling, harness-pod lifecycle, results copy, optional cloud upload to `gs://` / `s3://`) is also handled by the CLI based on your flags.

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
> An early termination of the installation could happen without clear error messages. The installer uses `sudo` to place `helm`, `helmfile`, `kubectl` and `oc` in `/usr/local/bin`; check that they exist there and are executable (`+x`).

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
    --workload       sanity_random.yaml
```

The individual guide README tells you how to set `${ENDPOINT_URL}`, `${GATEWAY_CLASS}`, the spec name (e.g. `guides/optimized-baseline`), and which workload profile to start with. Everything else on this page explains why each flag is there and what else you can pass.

## Anatomy of an invocation

Every `llmdbenchmark run` against a deployed stack follows the same shape: top-level flags before `run`, run-subcommand flags after.

| Position | Flag | Purpose |
|---|---|---|
| **Top-level** | `--spec guides/<guide-name>` | Which scenario this stack matches. Resolves to `config/specification/guides/<guide-name>.yaml` inside the repo. Required even in run-only mode because the CLI re-renders a plan to drive the harness pod. |
| Top-level | `--workspace <dir>` | (Optional) Where rendered configs, logs, and collected results land on your local machine. If omitted, an auto-generated timestamped directory is used and its path is printed in the logs at the end of the run. |
| Top-level | `--non-admin` | (Optional) Skip cluster-admin sanity checks. Use when running against a namespace where your user doesn't have cluster-wide privileges. |
| Subcommand | `run` | Selects the run subcommand. |
| `run` flag | `--endpoint-url "$URL"` | Skips auto-detection of the inference endpoint. Use the URL you resolved per the [Resolving the endpoint](#resolving-the-endpoint) section. |
| `run` flag | `--gateway-class <class>` | Tells the CLI which topology the cluster is running so its re-rendered plan matches reality. `epponly` (in addition to `standalone`) for Standalone Mode, `istio` / `agentgateway` / `gke` etc. for Gateway Mode. |
| `run` flag | `--model "<model-id>"` | The served model name. Must match exactly what the EPP's `/v1/models` endpoint returns (e.g. `Qwen/Qwen3-32B`). |
| `run` flag | `--namespace "$NS"` | Where the harness launcher pod, the workload PVC, and llm-d-prism are deployed (typically the same namespace as the stack). |
| `run` flag | `--harness <name>` | Which harness driver to use. See [Supported harnesses](#supported-harnesses). |
| `run` flag | `--workload <profile.yaml>` | The workload profile to render and execute. See [Available workload profiles](#available-workload-profiles). |
| `run` flag | `--output local \| gs://bucket \| s3://bucket` | (Optional) Cloud upload destination on top of the local copy. Defaults to `local` (no upload). To choose a custom **local** path, use `--workspace`, not `--output`. |
| `run` flag | `--overrides "key=value,..."` | (Optional) Per-run overrides of workload-profile fields without editing the profile file. Keys are dotted paths into the profile YAML. See [Override profile fields per run](#2-override-profile-fields-per-run-option-b). |
| `run` flag | `--prism` / `--no-prism` | (Optional) Deploy or skip the [llm-d-prism](https://github.com/llm-d/llm-d-prism) dashboard in the namespace. Deployed by default (also skipped with `--no-pvc`). See [Analysis and performance data visualization](#analysis-and-performance-data-visualization). |
| `run` flag | `--monitoring` | (Optional) Scrape vLLM `/metrics` and capture pod logs during the run, and embed the collected metrics in the benchmark report. Off by default; without this flag, no vLLM server metrics are collected (the harness's own latency and throughput metrics are always recorded). See [Analysis and performance data visualization](#analysis-and-performance-data-visualization). |
| `run` flag | `--no-pvc` | (Optional) Run without the workload PVC and the data-access pod, for clusters where you cannot provision a `ReadWriteMany` PVC. Results are copied straight from each harness pod into the local workspace before the pod is deleted. llm-d-prism is not deployed in this mode. |
| `run` flag | `--no-cleanup` | (Optional) Keep the harness pods and their ConfigMaps after the run, so you can inspect logs, exec into the pods, or re-copy results. The next run removes them. |
| `run` flag | `--wait-timeout`, `--pvc-bind-timeout`, `--data-access-timeout` | (Optional) Per-phase timeouts. See [Timeouts](#timeouts). |

## Resolving the endpoint

`llmdbenchmark run` needs an `--endpoint-url` to know where to send traffic. How you get the URL depends on whether you deployed in **Standalone Mode** (no Kubernetes Gateway resource — EPP pod with an Envoy sidecar) or **Gateway Mode**.

**Standalone Mode** (the default in most guides):

```bash
# GUIDE_NAME is the release name your guide used for the EPP (e.g. optimized-baseline).
export GUIDE_NAME=<guide-name>
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=standalone
```

**Gateway Mode**:

```bash
# Use the Gateway name your guide created (llm-d-inference-gateway in most guides).
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"
# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

The `--gateway-class` flag is important: even in run-only mode, the CLI renders a plan, and without an explicit `--gateway-class` it will not accurately detect the mode the user desires for their benchmark. Pass `--gateway-class` to make the rendered plan agree with reality and avoid surprises in downstream steps.

## Available workload profiles

All [workload profiles](https://github.com/llm-d/llm-d-benchmark/tree/main/workload#available-profiles-by-harness) live in the `llm-d-benchmark` repository under [`workload/profiles/<harness>/`](https://github.com/llm-d/llm-d-benchmark/tree/main/workload/profiles). When you pass `--workload <name>` and `--harness <harness>`, the CLI looks up `workload/profiles/<harness>/<name>` (and falls back to `<name>.in` to match the `.yaml.in` template files in the repo).

Always pass the `.yaml` form (e.g. `--workload sanity_random.yaml`), not the bare name.

The `guide_<guide-name>_<n>.yaml` workload profiles are tuned per guide and reproduce the load ladder used to showcase its performance under load. Use one of these if you want to reproduce results published in llm-d blog posts. Use `workload/profiles/<harness>/sanity*.yaml` for quick, basic functionality ("smoke") tests.

### Authoring your own profile

Copy an existing `.yaml.in` template in `workload/profiles/<harness>/`, save the copy in the same directory under a new name (e.g. `my_profile.yaml.in`), edit it, and pass the `.yaml` form of its name to `--workload` (e.g. `--workload my_profile.yaml`). The CLI only looks up profiles in `workload/profiles/<harness>/`. The `REPLACE_ENV_*` tokens inside the template — `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL`, `REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL`, etc. — are substituted at run time from the CLI flags you supplied. No `envsubst` step is needed.

## Supported harnesses

Choose the [harness](https://github.com/llm-d/llm-d-benchmark/tree/main/workload#available-harnesses) whose workload profiles and recorded metrics match your analysis needs. All harnesses are wrapped by `llmdbenchmark` and produce harness-specific JSON and, except for `lm-eval`, the standardized harness-agnostic [benchmark report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md).

All harnesses except `lm-eval`, which measures accuracy, capture the common metrics (TTFT, ITL, TPOT, E2E latency, throughput) and write the harness-agnostic `benchmark_report,_stage_*.yaml` files, but each produces different native report files. See [Workspace and results layout](#workspace-and-results-layout) for an example of the files written to disk (using `inference-perf`).

## HuggingFace token handling

All llm-d guides require a HuggingFace token Secret named `llm-d-hf-token` in the deployed namespace, even when the model is not gated. A token is mandatory for gated models such as Llama; the harness pod uses it to download the tokenizer. The token is mounted as `HF_TOKEN` and `HUGGING_FACE_HUB_TOKEN` env vars on the harness pod.

If you followed your guide's `Prerequisites` section, this Secret already exists. The CLI does not create the Secret from a local `HF_TOKEN` env var, so if it is missing, create it yourself — see [`helpers/hf-token.md`](../../../helpers/hf-token.md) for the full reference, or this snippet:

```bash
export HF_TOKEN=<your-huggingface-token>
kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

## Workspace and results layout

Benchmark results land in the **workspace directory** on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs at the end of the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level flag (before the `run` subcommand):

```bash
llmdbenchmark --workspace <YOUR_DIR_HERE> --spec guides/<guide-name> run <remaining arguments…>
```

The on-disk layout after a successful run (illustrative example using the `inference-perf` harness):

```text
<workspace>/
└── runner-<timestamp>/
    ├── plan/                          # rendered scenario plan (YAML, manifests)
    ├── environment/                   # captured cluster context
    └── results/
        └── <experiment-id>_<i>/       # one directory per harness pod (<i> = 1..parallelism)
            ├── stage_0_lifecycle_metrics.json
            ├── stage_1_lifecycle_metrics.json
            ├── …                      # one per workload stage
            ├── summary_lifecycle_metrics.json
            ├── per_request_lifecycle_metrics.json
            ├── benchmark_report,_stage_*.yaml # standardized cross-harness reports
            ├── config.yaml            # resolved harness configuration
            ├── <profile-name>.yaml    # the rendered workload profile used
            ├── stdout.log
            └── stderr.log
```

The `benchmark_report,_stage_*.yaml` files use a harness-agnostic schema so you can compare runs across different harnesses. See [Benchmark Report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md) for the schema.

### Cloud upload (optional)

To mirror the local results to cloud storage as a safety net, pass `--output gs://your-bucket/prefix` or `--output s3://your-bucket/prefix`. The local copy is unaffected — the cloud upload is an additional step after collection.

> [!NOTE]
> `--output` does **not** accept arbitrary local paths. Anything that isn't `local`, `gs://...`, or `s3://...` will be rejected with `Unknown output destination`. Use `--workspace` to control the local landing directory.

## Analysis and performance data visualization

At the end of a run, the [analysis](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/analysis.md) "pipeline" is automatically executed inside the harness pod, building the benchmark reports and, when `--monitoring` is passed, embedding the collected vLLM metrics into them. Unless you pass `--no-prism` or `--no-pvc`, the CLI deploys an instance of [llm-d-prism](https://github.com/llm-d/llm-d-prism) in the same namespace as the harness pod, and the `BENCHMARK RUN SUMMARY` shows how to reach it: a URL on OpenShift, or a port-forward command on other clusters (see below).

Illustrative example:

```text
2026-10-12 16:13:10,043 - INFO    - ============================================================
2026-10-12 16:13:10,043 - INFO    - BENCHMARK RUN SUMMARY
2026-10-12 16:13:10,043 - INFO    - ============================================================
2026-10-12 16:13:10,044 - INFO    -   Harness:       inference-perf
2026-10-12 16:13:10,044 - INFO    -   Workload:      sanity_random.yaml
2026-10-12 16:13:10,044 - INFO    -   Model:         facebook/opt-125m
2026-10-12 16:13:10,044 - INFO    -   Namespace:     <namespace>
2026-10-12 16:13:10,044 - INFO    -   Mode:          full
2026-10-12 16:13:10,044 - INFO    -   Parallelism:   1
2026-10-12 16:13:10,044 - INFO    -   Treatments:    1
2026-10-12 16:13:10,044 - INFO    -     - inference-perf-1791835939-kqrtvz
2026-10-12 16:13:10,044 - INFO    -       [1/1] inference-perf-1791835939-kqrtvz_1 (<count> files)
2026-10-12 16:13:10,044 - INFO    -   Local results:  <workspace>/runner-<timestamp>/results
2026-10-12 16:13:10,044 - INFO    -   PVC results:   oc exec -n <namespace> $(oc get pod -n <namespace> -l role=llm-d-benchmark-data-access -o jsonpath='{.items[0].metadata.name}') -- ls /requests/
2026-10-12 16:13:10,433 - INFO    -   🔑 Analysis and Performance Data Visualization: http://llm-d-prism-<namespace>.apps.<cluster-name>.<cluster-domain>
2026-10-12 16:13:10,433 - INFO    - ============================================================
2026-10-12 16:13:10,434 - INFO    - ✅ Run complete (mode=full, harness=inference-perf).
2026-10-12 16:13:10,944 - INFO    - Run parameters stored in configmap/llm-d-benchmark-run-parameters (key=run-20261012T201310Z) in ns/<namespace>
```

The example above is adapted from real output on an OpenShift cluster (hence `oc` rather than `kubectl`), where llm-d-prism is exposed through a Route and the summary prints its URL. On other clusters there is no ingress for llm-d-prism, so the summary prints a port-forward command instead:

```text
🔑 Analysis and Performance Data Visualization: kubectl port-forward -n <namespace> svc/llm-d-prism 8080:<port> then open http://localhost:8080
```

Run the command, keep it running, and open `http://localhost:8080` in your browser.

For additional information, please consult `llm-d-prism` [documentation](https://github.com/llm-d/llm-d-prism/blob/main/README.md).

The **benchmark report** is the primary document used to exchange information about a benchmark execution in a reproducible and standardized way. Users can submit reports to a global, publicly accessible [results store](https://github.com/llm-d/llm-d-benchmark/blob/main/llmdbenchmark/results_store/README.md) by following the [submission policy](https://github.com/llm-d/llm-d-benchmark/blob/main/llmdbenchmark/results_store/SUBMISSION_POLICY.md).

## Customizing the workload

### 1. Pick a different shipped profile (option A)

The simplest path. Browse the [profile catalog](https://github.com/llm-d/llm-d-benchmark/tree/main/workload#available-profiles-by-harness), pick one that matches your workload shape, and pass it via `--workload`.

### 2. Override profile fields per run (option B)

To tweak a few values of a shipped profile without copying it, pass `--overrides` on the `run` subcommand. Each override is a `key=value` pair, and pairs are comma-separated. Keys are dotted paths into the profile YAML. A numeric path segment selects a list element. For example, `load.stages.0.rate` is the `rate` of the first stage.

To run `sanity_random.yaml` (inference-perf) at 5 requests per second for 120 seconds with 500 prompts:

```bash
llmdbenchmark \
    --spec           guides/<guide-name> \
    run \
    …other flags… \
    --harness        inference-perf \
    --workload       sanity_random.yaml \
    --overrides      "load.stages.0.rate=5,load.stages.0.duration=120,data.input_distribution.total_count=500"
```

Overrides are applied after the profile is rendered. A key whose parent path does not exist in the profile is not applied, and the CLI logs a warning for it, so check the run logs if a value doesn't seem to take effect. The same setting is available as the `LLMDBENCH_OVERRIDES` env var.

### 3. Author your own profile

For workloads no shipped profile matches — copy a `.yaml.in` template, edit it, and reference the resulting `.yaml` filename. See [Authoring your own profile](#authoring-your-own-profile) above.

## Timeouts

The run pipeline exposes three independent timeouts. Defaults are conservative; bump them when your cluster's StorageClass, image pull, or harness run is slower than the defaults assume.

| Flag | Default | Controls |
|---|---|---|
| `--wait-timeout` | inherited from scenario's `harness.waitTimeout` or 3600s | "Waiting for pods to start" (deploy harness) **and** "Waiting for pods to complete" (the actual benchmark run). Each phase gets the full budget separately, so the worst case is twice this value. Set to `0` to dispatch and return without waiting. Combine it with `--no-cleanup`; otherwise post-run cleanup deletes the harness pod while it is still running. Only partial results, if any, reach the local workspace; fetch the complete results from the workload PVC later (see the `PVC results:` line in the run summary). With `--no-pvc` there is no PVC to fetch from; the results stay in the kept pods, so copy them out yourself. |
| `--pvc-bind-timeout` | 240s | How long to wait for the harness workload PVC to reach the `Bound` phase. Bump for slow shared-storage backends (weka, gpfs, ceph) where first-bind in a fresh namespace can take several minutes. |
| `--data-access-timeout` | 120s | How long to wait for the data-access pod that mounts the workload PVC to become Ready. |

Example with all three lifted for a slower environment:

```bash
llmdbenchmark \
    --spec           guides/<guide-name> \
    run \
    …other flags… \
    --wait-timeout         7200 \
    --pvc-bind-timeout     1200 \
    --data-access-timeout  600
```

All three are also exposed as env vars: `LLMDBENCH_WAIT_TIMEOUT`, `LLMDBENCH_PVC_BIND_TIMEOUT`, `LLMDBENCH_DATA_ACCESS_TIMEOUT`. `--pvc-bind-timeout` and `--data-access-timeout` have no effect with `--no-pvc`.

## Cleaning up benchmark resources

The CLI tears down the harness launcher pod and its ConfigMaps after each run (unless you pass `--no-cleanup`) but leaves the workload PVC, its supporting resources, and the [llm-d-prism](https://github.com/llm-d/llm-d-prism) dashboard in the namespace, so workload data and the dashboard survive between runs. The PVC is backed by shared storage (on GKE, a Filestore instance) that bills until removed. When you are done benchmarking in a namespace, delete the leftovers.

If every run in the namespace used `--no-pvc`, no workload PVC, data-access pod, or llm-d-prism was created, so most of the commands below find nothing to delete; they are still safe to run because of `--ignore-not-found`.

Delete llm-d-prism first. It mounts `workload-pvc`, so the PVC stays in `Terminating` until the dashboard is gone (on OpenShift, this also removes its Route):

```bash
kubectl delete deployment,service,route -n <namespace> --ignore-not-found \
  -l app.kubernetes.io/name=llm-d-prism
```

On clusters without the OpenShift `Route` API, drop `route` from the resource list.

Then delete the harness resources:

```bash
kubectl delete -n <namespace> --ignore-not-found \
  pod/access-to-harness-data-workload-pvc \
  service/llm-d-benchmark-harness \
  pvc/workload-pvc \
  configmap/llm-d-benchmark-preprocesses \
  configmap/llm-d-benchmark-run-parameters
```

If you used `--no-cleanup`, also delete the kept harness pods and their ConfigMaps (replace `<harness>` with the harness name, e.g. `inference-perf`):

```bash
kubectl delete pod -n <namespace> --ignore-not-found -l app=llmdbench-harness-launcher
kubectl delete configmap -n <namespace> --ignore-not-found <harness>-profiles llmdbench-harness-scripts
```

Afterward `kubectl get pvc -n <namespace>` should not list `workload-pvc`, and `kubectl get pods -n <namespace> -l app.kubernetes.io/name=llm-d-prism` should return nothing.

## Troubleshooting

### `did not return expected model 'X'. Available models: ['Y']`

The model name passed to `--model` doesn't match what the EPP is actually serving. Verify with:

```bash
kubectl run curl-probe -n "${NAMESPACE}" --rm -it --restart=Never \
    --image=cfmanteiga/alpine-bash-curl-jq \
    -- sh -c "curl -s ${ENDPOINT_URL}/v1/models | grep -o '\"id\":\"[^\"]*\"'"
```

Pass the exact ID it prints to `--model`.

### `Unknown output destination: ./results`

You passed a local filesystem path to `--output`. `--output` only accepts `local`, `gs://...`, or `s3://...`. Drop the flag (results still land locally in the workspace) or use `--workspace /your/path` to choose a custom local directory.

### `Timed out after 240s waiting for workload PVC`

Your cluster's StorageClass takes longer than 240s to bind a fresh PVC. Pass `--pvc-bind-timeout 1200` (or longer) on the `run` subcommand. Worth verifying the StorageClass behavior with `kubectl get sc` and `kubectl describe pvc` to confirm it's not a deeper issue (no default StorageClass, quota exhausted, etc.).

### `ProvisioningFailed ... multi writer with mount access type` on the workload PVC

The harness's `workload-pvc` requests `ReadWriteMany`, and your cluster's default StorageClass only provisions `ReadWriteOnce` volumes. The PVC must be RWX because several pods mount it: the data-access pod, the harness pod, and llm-d-prism. The run surfaces this as a data-access-pod timeout; the real error is in `kubectl describe pvc workload-pvc -n <namespace>`.

Point the scenario at an RWX-capable StorageClass by setting `storage.workloadPvc.storageClassName` in the benchmark scenario file (`config/scenarios/guides/<guide-name>.yaml`).

On GKE, the default `standard-rwo` (Persistent Disk) class is RWO-only. Enable the Filestore CSI driver and use its `standard-rwx` class:

```bash
gcloud container clusters update <cluster> --update-addons=GcpFilestoreCsiDriver=ENABLED
```

Then set `storage.workloadPvc.storageClassName: standard-rwx` in the scenario. First-time Filestore provisioning can take up to ~10 minutes (a zonal instance typically binds in 2-3 minutes), so pass `--pvc-bind-timeout 1200 --data-access-timeout 1200`.

If a run already failed on the wrong class, delete the stale claim before retrying. A PVC's StorageClass is immutable, and the CLI reuses an existing claim. Delete llm-d-prism first, since it also mounts `workload-pvc` and would otherwise keep the PVC stuck in `Terminating`:

```bash
kubectl delete deployment,service,route -n <namespace> --ignore-not-found \
  -l app.kubernetes.io/name=llm-d-prism
kubectl delete pod/access-to-harness-data-workload-pvc pvc/workload-pvc -n <namespace>
```

On clusters without the OpenShift `Route` API, drop `route` from the first command. The next run redeploys llm-d-prism.

If no RWX-capable StorageClass is available, pass `--no-pvc` on the `run` subcommand instead. The run then skips the workload PVC and the data-access pod and copies results directly from the harness pods, but llm-d-prism is not deployed.

### Harness pod stuck `Pending` with `Insufficient cpu`

The harness launcher pod is CPU-heavy — by default it requests 16 CPUs ([resource requirements](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/resource_requirements.md), tunable via `LLMDBENCH_HARNESS_CPU_NR`). It stays `Pending` until a node has enough allocatable capacity; check the scheduling events for the exact shortfall: `kubectl describe pod -l app=llmdbench-harness-launcher -n <namespace>`.

### `Could not detect endpoint for <stack>`

You didn't pass `--endpoint-url`, and the run-phase's auto-detection couldn't find the service. In run-only mode against an already-deployed stack, always pass `--endpoint-url` (and `--gateway-class`, see [Resolving the endpoint](#resolving-the-endpoint)).

### Harness can't download the tokenizer for a gated model

The harness pod mounts the `llm-d-hf-token` Secret as optional, so it starts even when the Secret is missing, and then fails when it tries to download the tokenizer of a gated model. Check that the Secret exists in the harness namespace:

```bash
kubectl get secret llm-d-hf-token -n <namespace>
```

If it is missing, create it as shown in [HuggingFace token handling](#huggingface-token-handling), then re-run.

### Harness pod hangs at "ContainerCreating" or "ImagePullBackOff"

The first run on a node has to pull the harness image, which can be slow. Pass `--wait-timeout 7200` to give the kubelet time. If the pull is failing entirely, check the pod's events with `kubectl describe pod -l app=llmdbench-harness-launcher -n <namespace>` — common causes are missing image-pull secrets on the namespace or a private registry without credentials.

### `unrecognized arguments: --pvc-bind-timeout` (or similar)

Your installed `llmdbenchmark` predates the flag. Check the version with `llmdbenchmark --version`. To update, pull the latest `main` into your clone (`git -C llm-d-benchmark pull`) and re-run the [install one-liner](#installation) from the directory that contains `llm-d-benchmark/`. The installer reuses an existing clone and does not update it. CI environments that pin to a specific tag may need to be bumped to a release that includes the flag.

---

For everything beyond this — DoE experiments with full stack standup/teardown cycles, custom harness images, multi-cluster runs, recommendations — see the [llm-d-benchmark repository](https://github.com/llm-d/llm-d-benchmark) directly.
