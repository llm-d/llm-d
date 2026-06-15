# Calibrating `peakPrefillThroughput`

Router configs that use the `prefix-cache-affinity-filter` plugin set
`peakPrefillThroughput` on it. The filter uses this value to estimate per-endpoint
time-to-first-token from in-flight load, which drives prefix-cache-aware routing.

The value is **hardware- and model-specific** — the plugin default (`15928`) is
calibrated for Qwen 32B on H100 80 GB (TP=2). If you deploy a different model or accelerator, measure your
own with this tool and set it on the `prefix-cache-affinity-filter` plugin in your
guide's router values file. (The agentic-serving guide ships `16444`, measured for
Qwen3-Coder-480B-FP8 on TPU v7x.)

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
