# Async multi-tenant nightly validator

Validator for the two scheduled async multi-tenant lanes:
`nightly-e2e-async-multitenant-evictable-gke-acc-gpu-vllm-x` (in-flight
eviction, the guide's default router values) and
`nightly-e2e-async-multitenant-gke-acc-gpu-vllm-x` (priority holdback, the
alternative). It proves that realtime traffic through llm-d-router keeps its TTFT, latency and
throughput while llm-d-async holds a backlog against the same pool, and that
llm-d-async alone drives the pool to saturation.

The stack is the [multitenant async-processing guide](../../../../guides/batch-serving/asynchronous-processing/multitenant/README.md)
as deployed by its `scripts/nightly-deploy-gke.sh` from the guide's
`guide.yaml`, including the llm-d-router coordinator with the `async-broker` step in front of the router. The coordinator
is what makes queued traffic producible over HTTP: a request with
`x-llm-d-async-mode: wait` is written to an llm-d-async queue and the
connection is held until llm-d-async has dispatched it and the result is back;
`x-llm-d-async-mode: passthrough` forwards live. Both are stamped with an InferenceObjective server
side (`realtime` tenant -> `reserved-interactive`, `batch` tenant ->
`reserved-batch` / `overflow-batch`), so clients cannot self-assign priority.

The coordinator is the guide's optional step 4 (an HTTP front door). It queues its tenants on the guide's own team queues (the async
traffic here goes to `team-batch` in the 16-worker `teams` pool), so it is
treated exactly like traffic published there directly.

## Topology

```text
inference-perf harness pods ──HTTP──> llm-d-coordinator:8080 ──> llm-d-router-epp:80 ──> vLLM
        (realtime: passthrough)             │  async-broker step
        (async: wait)                       └──> Redis queue team-batch ──> llm-d-async ──> llm-d-router-epp:80
```

## What runs

`run.py` installs the llm-d-benchmark CLI on the runner and executes one
`llmdbenchmark run --endpoint-url` experiment (`experiment.py` renders it) with
concurrent treatment groups:

| Group | Members | Purpose |
| --- | --- | --- |
| `async_only` | async | pool saturation and dispatch rate with no realtime traffic |
| `baseline_<L>` | realtime at `k_L` | reference for level L |
| `mixed_<L>` | async + realtime at `k_L` | the isolation measurement for level L |

`L` is the realtime saturation level in percent of the router's configured
capacity `C = concurrency-detector maxConcurrency x vLLM replicas` (10 x 1 in
the guide values); `k_L = round(L/100 x C)` = 2, 8, 9, 10 for 20/80/90/100 %.
Realtime is a closed-loop `load.type: concurrent` stage, so `k` is exact. The
async member is a constant-rate open-loop stage far above capacity with a
client timeout of `max(20, 4 x SERVICE_S)` seconds: undispatched requests are cancelled when the client gives up
(verified: llm-d-async never dispatches them even after claiming them from
Redis), so every async stage lasts `duration + timeout` seconds whatever the
realtime level, and leaves no backlog behind. The coordinator queues dispatch
from their own 16-worker llm-d-async pool so async alone can exceed `C`.

The two members of a mixed group can start minutes apart when their pods land
on freshly provisioned nodes. The async member is therefore listed first and
its stage lasts `REALTIME_SECONDS + START_SKEW_S` (at least 120 s), so the
realtime member runs inside the backlog. If a level still comes back unusable
(a treatment missing, or fewer than `MIN_SAMPLES` realtime completions while
both members were active), run.py re-runs that level's baseline and mixed
groups once (`RETRIES`), and async-only too if it is missing. A re-run level
replaces the first one only when all three of its treatments came back, so a
level's members always come from the same run. llm-d-benchmark's own per-treatment retry stays off,
because a retried group member would run alone.

Profiles: `guide_async-multitenant_1.yaml.in` (realtime) and `_2.yaml.in`
(async), in llm-d-benchmark's `workload/profiles/inference-perf/`. The
validator checks that the installed llm-d-benchmark has them and stops with a
clear error otherwise, for example when `AMT_BENCH_REPO` points at a fork that
predates them.

## Checks

Per level, from each treatment's `per_request_lifecycle_metrics.json`, on the
window where both members were active (minus a 15 s warm-up):

| Check | Default bound | Env key |
| --- | --- | --- |
| TTFT p50 and p90 | mixed <= 1.5 x baseline + 0.3 s | `TTFT_FACTOR`, `TTFT_ABS` |
| E2E p50 and p90 | mixed <= 1.25 x baseline + 0.3 s | `E2E_FACTOR`, `E2E_ABS` |
| req/s and output tok/s | mixed >= 0.85 x baseline (0.75 at L=20) | `RPS_FACTOR`, `TPS_FACTOR` |
| realtime errors | none | fixed |
| streaming sanity | baseline TTFT p50 < 0.6 x E2E p50 | `STREAM_SANITY_RATIO` |
| slack is used | async completions during mixed(20) > 0 | fixed |
| pool held at capacity (informational) | max vLLM running requests <= C + 1 during the mixed window (and async-only); reported but does not fail the run unless `AMT_ENFORCE_CAPACITY=1` | `CAPACITY_SLACK`, `ENFORCE_CAPACITY` |

Async-only: `vllm:num_requests_running` max >= 0.9 C x ceiling and p50 >=
0.8 C x ceiling during the async-only treatment, some client completions, an
EPP-side dispatch rate for the async bands (from the cumulative
`flow_control_requests_total` counters) >= 0.7 x baseline(100) req/s x
ceiling, and every completion has the
configured 128 tokens (server-reported usage). The EPP counter is the
throughput figure because the open-loop async client abandons queued requests
by design and its own completion count understates what the pool processed.
`ceiling` is priority holdback's `minCeiling` from the router values (0.5 in
`flow-control-holdback.yaml`), the share of capacity the router admits
`overflow-batch` to, or 1 without holdback. Finally the same counters must show
traffic in band 100 and in the batch bands (30 / -10).

Cluster metrics come from run.py's own sampler: a curl pod it polls every
`SAMPLE_INTERVAL_S` seconds for the whole run (vLLM running/waiting requests,
EPP pool saturation and per-band counters), attributed afterwards to each
treatment's `harness_start`/`harness_stop` window. llm-d-benchmark's
`--monitoring` is not used: its in-pod collector found no model pods in
run-only mode, and per-flow EPP histograms are garbage-collected between
treatments, so an end-of-run scrape misses them.

Why the bounds are not "zero degradation": an already-queued async request
can take a freed slot before the closed-loop realtime client resends, and at
20 % the vLLM batch grows from 2 to 10, so realtime pays a little TTFT and
throughput even when isolation works. The bounds allow that and fail on
anything worse.

The lane depends on the router protecting realtime traffic: without priority
holdback or in-flight eviction the router admits async work up to and past
`C` and realtime waits a full service time in flow control (TTFT p90 about
10 s on an L4), failing the isolation checks. Measurements are in
[llm-d-async#468](https://github.com/llm-d/llm-d-async/issues/468).
`AMT_FLOW_CONTROL` selects the guide's router values, for the deploy script and
the validator alike: `evictable` (default, `flow-control-evictable.yaml`) or
`holdback` (`flow-control-holdback.yaml`). The `pool held at
capacity` observation reports the router's admission overshoot. It is
informational until llm-d-router counts admissions before scheduling;
`AMT_ENFORCE_CAPACITY=1` makes it fail the run.

Set `AMT_<KEY>` to change a bound globally or `AMT_LEVEL_<L>_<KEY>` for one
level. The evictable lane sets `AMT_LEVEL_100_TTFT_ABS=0.6`: at 100 % realtime
load a realtime request that finds an async request in its slot waits for an
eviction round trip (up to about 0.5 s on H100s).

## Other knobs (`AMT_*`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `LEVELS` | `20,80,90,100` | saturation levels to run |
| `FLOW_CONTROL` | `evictable` | the guide's router values the deploy script installed (`evictable` or `holdback`); `C` and the async ceiling are read from that file |
| `CAPACITY` | from the router values x ready replicas | override `C` |
| `REALTIME_SECONDS` / `SERVICE_S` | `80` / `2.0` | size realtime stages (`num_requests = k x REALTIME_SECONDS / SERVICE_S`) |
| `ASYNC_RATE` / `ASYNC_DURATION` / `ASYNC_TIMEOUT` | `max(10, 2 x C / SERVICE_S)` / `max(120, REALTIME_SECONDS + START_SKEW_S)` / `max(20, 4 x SERVICE_S)` | async member load: arrivals at twice what the pool completes keep a backlog queued; the timeout must let dispatched requests finish |
| `START_SKEW_S` | `60` | how much later than the async pod the realtime pod may start and still run inside the backlog (use 180 on GKE Autopilot, where each harness pod may get a new node) |
| `RETRIES` | `1` | re-runs of levels whose comparison came back unusable (0 disables) |
| `WAIT_TIMEOUT` | `900` | seconds the CLI waits for a treatment before giving up (cuts off a hung client) |
| `WARMUP_S` | `15` | seconds trimmed from the front of every window |
| `WORKDIR` | `/tmp/amt` | CLI clone, workspace and rendered experiment |
| `RESULTS_DIR` | `/tmp/pod-logs-$GUIDE_NAME` | where `report.json` and results are copied (the reusable uploads this directory) |
| `COORDINATOR_HOST` | `llm-d-coordinator.<ns>.svc.cluster.local:8080` | endpoint the harness targets |
| `EPP_HOST` | `$GATEWAY_HOST` or `llm-d-router-epp` | EPP service for the final metrics scrape |
| `HARNESS_CPU` / `HARNESS_MEMORY` / `HARNESS_MEMORY_LIMIT` | `2` / `2Gi` / `4Gi` | harness pod resources (small so a mixed group's two pods schedule together) |
| `SAMPLE_INTERVAL_S` | `5` | cluster metrics sampling period |
| `VLLM_SELECTOR` / `VLLM_PORT` | `llm-d.ai/guide=async-multitenant,llm-d.ai/role=decode` / `8000` | model server pods and Deployments (the guide's step 1) the validator inspects and the sampler scrapes |
| `DRY_RUN_REQUESTS` / `DRY_RUN_ASYNC_DURATION` | `10` / `45` | sizes for `--dry-run` |
| `SKIP_INSTALL` | unset | reuse an existing clone at `$WORKDIR/llm-d-benchmark` |
| `BENCH_REPO` / `BENCH_REF` | upstream / `main` | llm-d-benchmark source to install, e.g. a fork carrying profile changes |
| `SETTINGS_FILE` | `$OUTPUT_DIR/validator.env` | settings file written by the deploy script (see below) |

## Passing settings in CI

`e2e-validate.sh` forwards only `-n` and `-m` to this validator, so settings
travel through a file instead: the guide's `scripts/nightly-deploy-gke.sh`
writes every `AMT_*` and `LLMDBENCH_*` variable it was started with
(credential-like names excluded) to `$OUTPUT_DIR/validator.env`, and run.py
loads it at startup. The process environment wins over the file, and
`--set KEY=VALUE` wins over both. To change settings for a CI run, prefix the
deploy command in the workflow, for example to install llm-d-benchmark from a
fork:

```yaml
custom_deploy_script: AMT_BENCH_REPO=https://github.com/<you>/llm-d-benchmark.git AMT_BENCH_REF=<branch> bash guides/batch-serving/asynchronous-processing/multitenant/scripts/nightly-deploy-gke.sh
```

The defaults assume H100s (about 2 s per 128-token request from `Qwen/Qwen3-32B`
with tensor parallelism 2). On slower GPUs scale the timing, for example:

```yaml
custom_deploy_script: AMT_SERVICE_S=6 AMT_REALTIME_SECONDS=150 AMT_START_SKEW_S=180 bash guides/batch-serving/asynchronous-processing/multitenant/scripts/nightly-deploy-gke.sh
```

## Running locally

Against any cluster where the stack is up (the deploy script, or the guide's steps 1 to 4 including the coordinator):

```bash
cd .github/scripts/e2e
GATEWAY_HOST=llm-d-router-epp ./e2e-validate.sh -n <namespace> -v --extra-validate async-multitenant
# or the validator alone, one short baseline group only:
python3 async-multitenant/run.py -n <namespace> -m Qwen/Qwen3-32B --dry-run
# settings on the command line, e.g. two levels on slower GPUs:
python3 async-multitenant/run.py -n <namespace> --set AMT_LEVELS=20,100 --set AMT_SERVICE_S=6 --set AMT_REALTIME_SECONDS=150
```

Unit tests (stdlib only):

```bash
python3 -m unittest discover -s .github/scripts/e2e/async-multitenant -p 'test_*.py' -v
```

## Artifacts

`$RESULTS_DIR/report.json` (summaries, ratios, checks), `llmdbenchmark.log`
(plus `llmdbenchmark-retry<N>.log` when a level was re-run), `experiment.yaml`, `epp-metrics.txt`, `coordinator.log`, and a copy of every
treatment's results directory. In CI the run's step summary shows the
per-level table and the check list.
