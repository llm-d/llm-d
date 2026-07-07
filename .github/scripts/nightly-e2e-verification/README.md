# Nightly E2E verification scripts

Verification scripts for running in the verification step in the nightly E2E CI workflow.

Verification runs between the harness `Run` step and `Teardown` in
`reusable-ci-nightly-benchmark.yaml` (llm-d-infra), so pods and PVCs are
still up and `kubectl` is configured against the test cluster.

## Layout

```
nightly-e2e-verification/
├── README.md
├── verify-locally.sh            ← wrapper: sets env vars, runs a verify.py on your laptop
├── verify_helpers.py         ← shared helpers (library only; not run directly)
├── _template/
│   └── verify.py             ← copy this for a new scenario
└── tiered-prefix-cache/
    └── verify.py             ← inline metric checks + PVC inspection
```

## Wiring up a caller workflow

Point at the per-scenario script:

```yaml
uses: llm-d/llm-d-infra/.github/workflows/reusable-ci-nightly-benchmark.yaml@main
with:
  verify_script: .github/scripts/nightly-e2e-verification/tiered-prefix-cache/verify.py
```

If `verify_script` is empty, no verification runs.

## Running locally

Use `verify-locally.sh` after an `llmdbenchmark run` finishes. Point it at the
same workspace dir you passed to llmdbenchmark — the wrapper will pick the
newest `<user>-<timestamp>/results/<exp>` under it.

```bash
# Pass the workspace explicitly
./verify-locally.sh tiered-prefix-cache ~/llmdbenchmark

# Or export it once and skip the second arg
export LLMDBENCH_WORKSPACE=~/llmdbenchmark
./verify-locally.sh tiered-prefix-cache
```

The wrapper sets the same `LLMDBENCH_*` env vars the CI job sets. Only
`LLMDBENCH_WORKSPACE` is required; the rest are inferred:

- `LLMDBENCH_CICD_NS` ← `kubectl`current context,
  falling back to `default`
- `LLMDBENCH_CICD_SCENARIO` ← the scenario dir name
- `LLMDBENCH_CICD_{WORKLOAD,HARNESS,DETECTED_MODEL}` ← `<local>` placeholder

Any env var already exported in your shell wins over the wrapper's default,
so overriding one is just `LLMDBENCH_CICD_NS=my-ns ./verify-locally.sh …`.

## Adding a new scenario

```bash
cp -r _template <your-scenario>
$EDITOR <your-scenario>/verify.py
```

Then in `<your-scenario>/verify.py`:

1. **Inline checks.** Add `metrics.check_aggregated(...)` and/or
   `metrics.check_per_pod(...)` calls to the `checks` list. Aggregates and
   ops are listed below.
2. **Scenario-specific inspection (optional).** Add `print()`s and custom
   `v.Check(...)` objects — see [tiered-prefix-cache](tiered-prefix-cache/verify.py)
   for a worked example.
3. Point the caller workflow at the new path.

The script is the single source of truth — grep-friendly, commentable per
line, no separate config file to drift.

## API reference

```python
import verify_helpers as v

env         = v.workflow_env()                            # dict of env vars
exp_dirs    = v.find_results_dirs(env["workspace"])       # list of experiment subdirs
metrics     = v.MetricsSummary.load(exp_dirs[0])          # wraps metrics_summary.json
```

### `MetricsSummary`

Three views over `metrics_summary.json`:

```python
metrics.raw          # the entire loaded JSON (escape hatch)
metrics.aggregated   # {metric_name: {mean, max, p99, ...}}   ← _aggregated.metrics
metrics.per_pod      # {pod_name: {metric_name: {mean, max, ...}}}
```

Two check factories:

```python
# Reads metrics.aggregated[metric][aggregate], threshold-checks it.
metrics.check_aggregated("vllm:time_to_first_token_seconds", "p99", "<=", 2.0)
# -> Check(name="vllm:time_to_first_token_seconds.p99", passed=..., detail="1.85 <= 2.00")

# Pulls metrics.per_pod[pod][metric][aggregate] for every pod, combines with
# combine(values), threshold-checks the result. Default combine function is max
# ("did any pod hit the bound?"). Pass any callable that consumes an iterable
# of floats: max, min, sum, statistics.mean, or a lambda around functools.reduce.
metrics.check_per_pod("vllm:kv_offload_store_bytes_total", "max", ">", 0.0)
metrics.check_per_pod("vllm:kv_cache_usage_perc", "mean", "<=", 80.0, combine=statistics.mean)
# -> Check(name="vllm:foo.max (per-pod max)", passed=..., detail="1.02e+09 > 0")
```

### Adding additional checks

Construct a `v.Check` directly:

```python
n = number_of_files_in_pvc(...)
v.Check("PVC has KV cache data", passed=n > 0, detail=f"{n} files at /mnt/kv-cache")
```

### Verify and print output

```python
# Prints the standard report + returns True/False.
passed = v.verify_checks(env, metrics, checks)
sys.exit(0 if passed else 1)
```

### Aggregates and ops

`process_metrics.py`'s `_compute_stats` produces:
`mean, stddev, min, p25, p50, p75, p90, p95, p99, max, count`

Ops: `<=, >=, <, >, ==`

## Workflow env values

`workflow_env()` renames the useful ones to short keys. The rest are read
directly from `os.environ` when needed.

| Variable | `env[]` key | Meaning |
|---|---|---|
| `LLMDBENCH_WORKSPACE` | `env["workspace"]` | Runner workspace; results dir lives below here |
| `LLMDBENCH_CICD_NS` | `env["namespace"]` | Namespace used for the run (use for `kubectl -n`) |
| `LLMDBENCH_CICD_SCENARIO` | `env["scenario"]` | Scenario name (e.g. `tiered-prefix-cache`) |
| `LLMDBENCH_CICD_WORKLOAD` | `env["workload"]` | Workload filename (e.g. `tiered-prefix-cache.yaml`) |
| `LLMDBENCH_CICD_HARNESS` | `env["harness"]` | Harness name (e.g. `inference-perf`) |
| `LLMDBENCH_CICD_DETECTED_MODEL` | `env["model"]` | Model id |
| `GITHUB_RUN_ID` | `env["run_id"]` | For traceability |
| `LLMDBENCH_CICD_OFFLOADING_TARGET` | _read via `os.environ`_ | `'fs'` / `'cpu'` (tiered-prefix-cache mode) |
| `KUBECONFIG` / `~/.kube/config` | _used by `kubectl`_ | kubectl is already configured |

Exit code: `0` on success, non-zero on any failure. Read the CI logs to see
why.

## Required precondition: monitoring on

Any script that reads `metrics_summary.json` requires the harness to scrape
vLLM `/metrics` during the run (`metricsScrapeEnabled: true`). The reusable
workflow forces this on whenever `verify_script` is set — see the
`prereqs_setup_extrac_cli_parms_llmdbenchmark` step. If a caller sets both
`verify_script` and `monitoring_enabled: false`, the workflow fails fast
at the prereqs stage.
