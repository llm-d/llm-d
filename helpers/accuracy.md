# Accuracy validation with lm-evaluation-harness

This helper runs [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness)
(`lm_eval`) against a deployed llm-d guide to validate model **accuracy**
(zero/few-shot task scores). It complements [benchmark.md](benchmark.md), which
covers **performance** benchmarks via the [llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark)
framework.

`lm-eval` is not a `llm-d-benchmark` harness; it talks to the deployed stack
through the OpenAI-compatible `/v1/completions` endpoint that vLLM exposes. No
PVC, launcher pod, or framework image is required.

## Requirements

- A guide stack already deployed in your cluster (see each guide's README).
- `kubectl` configured for that cluster.
- Python 3.9+ with `lm-eval` installed:

    ```bash
    pip install lm-eval
    ```

## Run

```bash
export NAMESPACE=<your-namespace>

# optimized-baseline (Intel XPU overlay, Qwen3-0.6B)
./helpers/accuracy/run-lm-eval.sh -g optimized-baseline

# pd-disaggregation (Intel XPU overlay, Qwen3-0.6B, prefill/decode split)
./helpers/accuracy/run-lm-eval.sh -g pd-disaggregation

# precise-prefix-cache-routing (Intel XPU overlay, Qwen3-0.6B with KV-events)
./helpers/accuracy/run-lm-eval.sh -g precise-prefix-cache-routing
```

The script:

1. Loads `helpers/accuracy/presets/<guide>.env` (gateway label, model, tasks,
   few-shot, sample limit, concurrency).
2. Resolves the service to port-forward. It first honors `GATEWAY_SVC`, then
    tries `gateway.networking.k8s.io/gateway-name=<label>`, then falls back to
    the Standalone Mode service name used by guide READMEs: `<guide>-epp`.
3. Starts a background `kubectl port-forward` on `127.0.0.1:8000`.
4. Invokes `lm_eval --model local-completions --model_args base_url=...` with
   the preset values.
5. Writes per-task results to `./results/<guide>-<timestamp>/`.
6. Tears the port-forward down on exit.

## Per-guide defaults

| Guide                            | Tasks                                                              | num_fewshot | concurrency |
| -------------------------------- | ------------------------------------------------------------------ | ----------- | ----------- |
| `optimized-baseline`             | `gsm8k`, `lambada_openai`, `mmlu_high_school_mathematics`          | 0           | 4           |
| `pd-disaggregation`              | above + `hellaswag`                                                | 0           | 4           |
| `precise-prefix-cache-routing`   | `gsm8k`, `mmlu_high_school_mathematics`, `hellaswag`               | 5           | 8           |

Few-shot prompts in the precise-prefix-cache-routing preset share long
instruction prefixes across samples, which exercises the precise prefix-cache
router on the deployed stack.

Sample limit defaults to `LIMIT=50` per task to keep runtime bounded. For a
full quality run override it:

```bash
LIMIT= ./helpers/accuracy/run-lm-eval.sh -g optimized-baseline
```

## Overriding preset values

Any preset variable can be overridden via the environment without editing the
preset file:

```bash
TASKS=gsm8k NUM_FEWSHOT=8 LIMIT=20 \
  ./helpers/accuracy/run-lm-eval.sh -g optimized-baseline
```

To target an external endpoint instead of port-forwarding into the cluster:

```bash
BASE_URL=http://<gateway-host>:<port>/v1/completions \
  ./helpers/accuracy/run-lm-eval.sh -g optimized-baseline --no-port-forward
```

To force a specific Kubernetes service:

```bash
GATEWAY_SVC=<service-name> SERVICE_PORT=80 \
  ./helpers/accuracy/run-lm-eval.sh -g optimized-baseline
```

## Adding a new guide

Drop a new file at `helpers/accuracy/presets/<guide-name>.env` containing
`GATEWAY_LABEL`, `MODEL`, `TASKS`, `NUM_FEWSHOT`, `LIMIT`, `NUM_CONCURRENT`.
No script changes are needed.
