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
- `yq` (v4+) to read the per-guide config (see [Client Setup](client-setup/README.md)).
- Python 3.9+ with `lm-eval` (API extras) installed:

    ```bash
    pip install "lm-eval[api]" transformers
    ```

## Run

```bash
export NAMESPACE=<your-namespace>

# optimized-baseline (Intel XPU overlay, Qwen3-0.6B)
./helpers/accuracy/run-lm-eval.sh -g optimized-baseline
```

The script:

1. Reads the per-guide config at
   `guides/<guide>/lm-eval-templates/guide.yaml` (model, gateway label, tasks,
   few-shot, sample limit, concurrency).
2. Resolves the service to port-forward. It first honors `GATEWAY_SVC`, then
    tries `gateway.networking.k8s.io/gateway-name=<label>`, then falls back to
    the Standalone Mode service name used by guide READMEs: `<guide>-epp`.
3. Starts a background `kubectl port-forward` on `127.0.0.1:8000`.
4. Invokes `lm_eval --model local-completions --model_args base_url=...` with
   the config values.
5. Writes per-task results to `./results/<guide>-<timestamp>/`.
6. Tears the port-forward down on exit.

## Per-guide defaults

| Guide                | Tasks                          | num_fewshot | concurrency |
| -------------------- | ------------------------------ | ----------- | ----------- |
| `optimized-baseline` | `hellaswag`, `mmlu`, `piqa`    | 0           | 1           |

The sample limit defaults to whatever the per-guide config sets in
`.evaluation.limit`. The included `optimized-baseline` config uses `limit: null`
(a full run). To bound runtime, set `LIMIT=<n>` for a quick smoke test:

```bash
LIMIT=50 ./helpers/accuracy/run-lm-eval.sh -g optimized-baseline
```

## Overriding config values

Any config value can be overridden via the environment without editing the
config file:

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

Drop a config at `guides/<guide-name>/lm-eval-templates/guide.yaml` with
`.endpoint.model`, `.endpoint.gateway_label`, and `.evaluation.{tasks,
num_fewshot, limit, num_concurrent}`. No script changes are needed; run it with
`./helpers/accuracy/run-lm-eval.sh -g <guide-name>`.
