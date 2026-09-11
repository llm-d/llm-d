# GLM context-migration workload

This workload targets the two EPP arms over the shared DP8 GLM engine fleet:

- baseline: `glm-cache-route-epp-baseline.nilig-agentx-slo:80`
- candidate: `glm-cache-route-epp-candidate.nilig-agentx-slo:80`

Every script explicitly defaults to the `kermit_US-EAST-01A` Kubernetes
context. Set `KUBE_CONTEXT` to select a different cluster.

## Artifact storage

Create or reconcile the AgentX artifact PVC and access pod:

```bash
kubectl --context kermit_US-EAST-01A apply -f artifacts.yaml
kubectl --context kermit_US-EAST-01A -n nilig-agentx-slo \
  wait --for=condition=Ready pod/workload-access --timeout=5m
```

Runs are immutable directories below
`/workload/p2p-context-migration-glm`. A repeated `RUN_ID` fails instead of
overwriting evidence.

## Exact migration smoke

Use a unique salt for every invocation:

```bash
./run-smoke.sh baseline 31001
./run-smoke.sh candidate 41001
```

The seed contains exactly 102,400 token IDs. The follow-up contains the same
prefix plus 1,024 token IDs, for 103,424 total. Only the first 64-token block
depends on the salt; the remaining payload is identical between arms. This
isolates each run from an earlier engine cache while preserving a comparable
prompt.

Each result directory contains the request result, pre/post engine counter
snapshots and their delta, pre/post EPP metrics, live object snapshots, and EPP
logs. A valid candidate smoke has both HTTP statuses at 200, a P2P/external
prefix hit near 102,400 tokens on P-short, and local follow-up work near 1,024
tokens. The baseline should keep the follow-up on P-long.

Run the same smoke while creating observable load through the selected EPP:

```bash
BACKGROUND_REQUESTS=1 \
DIRECT_BACKGROUND_REQUESTS_PER_RANK=3 \
DIRECT_HTTP_PORT_BASE=28600 \
TRIGGER_MINIMUM_INFLIGHT_TOKENS=12548 \
REQUIRE_CAPACITY_QUEUED_RANKS=8 \
LOAD_WAIT_TIMEOUT=240 \
REQUEST_TIMEOUT=180 \
  ./run-smoke.sh baseline 51001

BACKGROUND_REQUESTS=1 \
DIRECT_BACKGROUND_REQUESTS_PER_RANK=3 \
DIRECT_HTTP_PORT_BASE=28600 \
TRIGGER_MINIMUM_INFLIGHT_TOKENS=12548 \
REQUIRE_CAPACITY_QUEUED_RANKS=8 \
LOAD_WAIT_TIMEOUT=240 \
REQUEST_TIMEOUT=180 \
  ./run-smoke.sh candidate 61001
```

The runner starts three synchronized, cache-isolated 115,200-token requests on
each P-long rank through its direct vLLM port. Direct requests avoid decode load
and are invisible to EPP accounting; they exist only to create a real capacity
queue on every P-long rank. The runner then sends one EPP-visible trigger that
reuses the 102,400-token seed and adds a private 12,800-token tail. Its
CPU-weighted affinity remains above 0.35, so it reaches the cached source rank,
and its 12,800 uncached tokens exceed the 12,548-token predicted-delay gate.
The follow-up is released only while all eight capacity queues and the source
rank's EPP in-flight signal are simultaneously present. This prevents an idle
P-long rank from confounding the baseline. A valid candidate contains `TTFT
load gate broken` and transfers the cached prefix to P-short; a valid baseline
opens the same gate but keeps the follow-up on P-long because it scores the full
context rather than subtracting reusable tokens.

## Sustained AgentX C64 run

The decode profile must use `session-affinity-filter` with the following
session sources and TTL in both router arms:

```yaml
sessionIdConfig:
  sources:
  - header: x-session-id
  - header: x-correlation-id
  evictionTtlSeconds: 3600
```

`x-session-id` keeps the exact smoke requests pinned. AgentX supplies a unique
`x-correlation-id` for each session and reuses it across that session's turns,
so the Job must not set a static correlation header. The 3,600-second TTL keeps
the decode binding alive across long GLM requests and the complete benchmark
window.

Run one arm:

```bash
./run-agentx.sh baseline
./run-agentx.sh candidate
```

The job uses the date-pinned `semianalysis_cc_traces_weka_062126` corpus,
random seed `20260707`, C64, a 120-second warmup, and a 900-second measured
window. `--cache-bust first-turn-prefix` gives every AgentX session tree a
private leading prefix while all turns within a tree retain their shared
prefix. The shared seed makes this cache bust deterministic across arms, so it
does not provide cross-arm isolation by itself.

The runner rejects overlapping context-routing Jobs, waits for all three GLM
engine pods to be idle, and restarts all three engine StatefulSets for every
arm. It records each old and new pod UID, requires every UID to change, waits
for each rollout and Ready condition, then restarts the selected EPP. The
engine restart clears cross-arm KV state; the EPP restart clears its in-memory
prefix, in-flight, and session state. Persistent model and compile caches remain
available to the replacement engine pods.

For a counterbalanced campaign, run one ABBA block:

```bash
./run-abba.sh
```

Every ABBA position uses the default `RESET_ENGINES=1`. `RESET_ENGINES=0` is
available only for diagnostic runs that are not used as cross-arm benchmark
evidence.

Increase `BLOCKS` for independent ABBA blocks:

```bash
BLOCKS=5 CAMPAIGN_ID=c64-five-blocks ./run-abba.sh
```

Do not overlap arms. Both EPP services share the same engines, and overlapping
runs would contaminate the engine counter deltas and queueing comparison.

A sustained arm is accepted only when the Kubernetes Job is `Complete` and
`validation.json` reports `valid: true`. The validator rejects nonzero AIPerf
status, request-level errors, cancellations, context-overflow skips,
unterminated records, missing metrics, missing or unstable session correlation,
static correlation IDs, scenario/config drift, and missing artifacts. This is
stricter than AIPerf's process status: saved C64 runs exist where AIPerf exited
zero while individual requests returned HTTP 400. The runner also requires
decode affinity binding logs with no decode session migrations, and rejects
engine or EPP UID/readiness/restart-count changes during the measured arm. It
saves the Job, EPP, InferencePool, pod, log, idle, metric, and counter evidence
alongside the AIPerf artifacts on the PVC.

## Local validation

These checks do not submit a Job:

```bash
bash -n run-smoke.sh run-agentx.sh run-abba.sh
python3 -m py_compile smoke.py counters.py validate_agentx.py
DRY_RUN=1 ./run-agentx.sh baseline > /tmp/glm-agentx-job.yaml
kubectl --context kermit_US-EAST-01A create \
  --dry-run=client --validate=false -f /tmp/glm-agentx-job.yaml -o name
```

Inspect a saved engine-counter pair after copying it from the PVC:

```bash
python3 counters.py diff counters-pre.json counters-post.json
```
