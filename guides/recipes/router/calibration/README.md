# Calibrating `peakPrefillThroughput`

Router configs that use the `prefix-cache-affinity-filter` plugin set
`peakPrefillThroughput` on it. The filter uses this value to estimate per-endpoint
time-to-first-token from in-flight load, which drives prefix-cache-aware routing.

The value is **hardware- and model-specific** — the plugin default (`15928`) is
calibrated for Qwen 32B on H100 80 GB (TP=2). If you deploy a different model or accelerator, measure your
own with this tool and set it on the `prefix-cache-affinity-filter` plugin in your
guide's router values file. (The agentic-serving guide ships `16444`, measured for
Qwen3-Coder-480B-FP8 on TPU v7x.)

See the [**configuration matrix**](./configuration-matrix.md) for the reference values by
(model, accelerator) shipped under `guides/`, and which combinations still need a calibration run.

## What it measures

`calibrate.sh` runs a short Kubernetes Job ([`calibration-peak-throughput.yaml`](calibration-peak-throughput.yaml))
that sends warmup + measurement requests of exactly `CHUNK_SIZE` random token IDs (so the
prefix cache misses every time and we measure true prefill), records TTFT, and computes:

```text
peakPrefillThroughput = CHUNK_SIZE / median(TTFT)   # tokens/sec
```

It **only measures and prints** the value — it does not modify any config.

## Prerequisites

- The stack is deployed and serving (router + model server), reachable from the Job's network.
- `kubectl` and `envsubst` on your `PATH`.

## Usage

```bash
GUIDE_NAME=agentic-serving \
NAMESPACE=llm-d-agentic-serving \
MODEL_NAME=Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 \
CHUNK_SIZE=8192 \
./calibrate.sh
```

| env var | meaning | default |
| --- | --- | --- |
| `GUIDE_NAME` | release/guide name (used for the `<name>-epp` service) | `optimized-baseline` |
| `NAMESPACE` | namespace the stack runs in | `default` |
| `MODEL_NAME` | model vLLM is serving | `Qwen/Qwen3-32B` |
| `CHUNK_SIZE` | request size; **must match vLLM `--max-num-batched-tokens`** | `8192` |
| `T_MAX_SECONDS` | TTFT SLO tolerance (informational `TAU` line only) | `18` |
| `VLLM_ENDPOINT` | `http://host:port`; auto-discovered from the EPP service if unset | — |
| `NUM_WARMUP` / `NUM_MEASUREMENTS` | request counts | `5` / `20` |

## Applying the value

Set the measured number on the `prefix-cache-affinity-filter` plugin in your guide's
router values file:

```yaml
- type: prefix-cache-affinity-filter
  parameters:
    peakPrefillThroughput: <measured value>
```

Then re-apply the router release (`helm upgrade ... -f <your-guide>.values.yaml`) and
restart the EPP:

```bash
kubectl rollout restart -n ${NAMESPACE} deployment/${GUIDE_NAME}-epp
```

## Files

| file | purpose |
| --- | --- |
| `calibrate.sh` | orchestration: runs the Job, extracts and prints the value |
| `calibration-peak-throughput.yaml` | the measurement Job + its Python script (ConfigMap) |
| `calibrate-min-cached-token-delta.sh` | measures the pull-versus-recompute crossover (`minCachedTokenDelta`) |
| `calibration-min-cached-token-delta.yaml` | the crossover Job + its Python script (ConfigMap) |
| `fit-cost-model.py` | fits the `p2p-source-producer` `costModel` constants from the crossover table and a loaded run |

## Calibrating `minCachedTokenDelta`

Router configs that use the `p2p-source-producer` (the
[p2p-kv-cache-sharing guide](../../../p2p-kv-cache-sharing/README.md)) set
`minCachedTokenDelta` on it: a pull is requested only when a peer holds at
least that many more cached prefix tokens than the scheduled pod. Below the
pull-versus-recompute crossover a pull costs more than recomputing, so the
right value is the crossover — and the crossover is **model-, hardware- and
transport-specific** (measured on gpt-oss-120b/H200: below 2K with `rdma/ib`
on the pods; calibrate separately on the TCP fallback).

[`calibrate-min-cached-token-delta.sh`](calibrate-min-cached-token-delta.sh)
runs a Job ([`calibration-min-cached-token-delta.yaml`](calibration-min-cached-token-delta.yaml))
that measures it against two live model-server pods: per length and
repetition it seeds a fresh random token-ID prompt on the source pod, then
times the consumer pod serving that prompt with and without
`kv_transfer_params.remote_kv_source` (independent prompts for each leg,
because the consumer caches whatever it just served). The mesh is warmed
first so the one-time session-establishment cost is excluded. It prints the
measured ladder and the recommendation:

```text
MIN_CACHED_TOKEN_DELTA=<smallest tested length where the pull won>
```

```bash
NAMESPACE=llm-d-p2p \
POD_SELECTOR=llm-d.ai/guide=p2p-kv-cache-sharing \
MODEL_NAME=openai/gpt-oss-120b \
./calibrate-min-cached-token-delta.sh
```

Unlike the peak-throughput Job this cannot go through the router — the pull
is driven by injecting `kv_transfer_params` directly at two engine
endpoints — so the script talks to pod IPs (`ENGINE_PORT`, default `8200`).
Prerequisites: the OffloadingConnector P2P tier on the pods,
`PYTHONHASHSEED` pinned fleet-wide, and the same transport you will deploy
on. Lengths must be multiples of the vLLM block size.

## Calibrating the `costModel`

The `p2p-source-producer` also accepts an optional `costModel` block that
replaces the fixed `minCachedTokenDelta` rule with a per-request comparison of
the TTFT a pull saves against what it costs. A pull is taken when

```text
delta*(P - T) + fleetWeight*R(d)*delta*P > F + busy(s)*S + busy(d)*Q
```

where `delta` is the extra cached tokens on the source, `R(d)` is the running
request count on the computing pod, and `busy(x)` is 1 when that pod's waiting
queue is at or above `busyQueueThreshold`.
[`fit-cost-model.py`](fit-cost-model.py) fits every constant except
`fleetWeight`, which weighs the prefill saved for other requests on the pod
against the requesting user's latency and is set by hand.

| constant | measured from |
| --- | --- |
| `prefillMicrosecondsPerToken` (P) | idle recompute slope |
| `transferMicrosecondsPerToken` (T) | idle pull slope |
| `transferFixedMs` (F) | loaded run: fixed extra TTFT of a pulled request |
| `sourceWaitMs` (S) | loaded run: extra TTFT of a pull from a busy source |
| `requeueMs` (Q) | loaded run: extra TTFT of a pull onto a busy computing pod |

It needs Python 3 only (no packages) and a router build whose
`p2p-source-producer` logs one `"p2p decision"` line per request at the
default log level.

1. **Idle rates (P, T).** Save the output of
   `calibrate-min-cached-token-delta.sh` (above). The script fits a line
   through the recompute and pull columns of its table.
2. **Loaded run (F, S, Q).** Deploy the router without a `costModel` block so
   the fixed-delta rule takes every candidate pull, then run the guide's
   benchmark at the concurrency you intend to serve. Set
   `report.request_lifecycle.per_request: true` in the inference-perf config
   and capture the decision lines for the length of the run:

   ```bash
   kubectl logs -f -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp -c epp --since=1s \
     | grep '"p2p decision"' > epp-decisions.log
   ```

   Each request in `per_request_lifecycle_metrics.json` is joined to its
   decision by request id (the vLLM completion id `cmpl-<id>` is the router
   request id). Two to four runs pooled give steadier values.
3. **Fit.**

   ```bash
   ./fit-cost-model.py \
     --crossover crossover.log \
     --per-request run1/per_request_lifecycle_metrics.json \
     --per-request run2/per_request_lifecycle_metrics.json \
     --epp-log epp-decisions.log \
     --busy-queue-threshold 1
   ```

   The `costModel` block goes to stdout and the diagnostics to stderr. The
   script exits with an error when T is not below P (the pull cannot win on
   that transport). It bootstraps the requests for 80% intervals on F, S and
   Q and warns when an interval is wider than 250 ms; treat a warned value as
   uncalibrated.

F, S and Q can only be separated when the loaded run pulls prefixes of
varied size under both idle and busy queues. A workload whose pulls are all
the same size confounds F with T. On gpt-oss-120b/H200 over TCP, the
document-Q&A benchmark (every pull a whole 48K-token document) gave P 33.4
and T 15.6 us/token, but 80% intervals of 709 to 1209 ms for F, -229 to
288 ms for S and -6 to 508 ms for Q. A shared-prefix or multi-turn workload
with prefixes from about 2K to 64K tokens is a better calibration run.
