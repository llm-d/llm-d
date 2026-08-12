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

```
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

## Calibrating `minCachedTokenDelta`

Router configs that use the `p2p-source-producer` (the
[p2p-kv-cache-sharing guide](../../../p2p-kv-cache-sharing/README.md)) set
`minCachedTokenDelta` on it: a pull is requested only when a peer holds at
least that many more cached prefix tokens than the scheduled pod. Below the
pull-versus-recompute crossover a pull costs more than recomputing, so the
right value is the crossover — and the crossover is **model-, hardware- and
transport-specific** (measured on gpt-oss-120b/H200: below 2K with `rdma/ib`
on the pods, near 29K on the TCP fallback).

[`calibrate-min-cached-token-delta.sh`](calibrate-min-cached-token-delta.sh)
runs a Job ([`calibration-min-cached-token-delta.yaml`](calibration-min-cached-token-delta.yaml))
that measures it against two live model-server pods: per length and
repetition it seeds a fresh random token-ID prompt on the source pod, then
times the consumer pod serving that prompt with and without
`kv_transfer_params.remote_kv_source` (independent prompts for each leg,
because the consumer caches whatever it just served). The mesh is warmed
first so the one-time session-establishment cost is excluded. It prints the
measured ladder and the recommendation:

```
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
