# Diagnose Speculative Decoding

Use this page when vLLM's speculative decoding (SD) counters are flat, missing, or
show an unexpected acceptance. It covers vLLM only; SGLang is not covered yet. The
metrics are described in [Model Server Metrics](./model-server-metrics.md#vllm-speculative-decoding-metrics)
and the queries are in the [PromQL Query Reference](./promql.md#speculative-decoding).

Each step below records one kind of evidence. None of them alone establishes a
cause, and a missing series is recorded as unknown, not filled in as 0.

## What each signal can and cannot show

| Signal | Evidence | Shows | Does not show |
| --- | --- | --- | --- |
| Configuration | Image digest, vLLM version, resolved speculative config in the server log | The SD config the running engine reports | That drafting happened, or output correctness |
| Scrape | `up`, raw `/metrics`, the scrape config | Whether the series are collected | That SD is off, from missing series alone |
| Drafting | Increase of drafts and draft tokens in the window | Drafting activity in that scope | That every request or step drafted |
| Acceptance | Accepted over draft tokens, same scope and window | Share of draft tokens accepted | Health, correctness, or end-to-end speedup |
| Selected K (optional) | Increase per `num_spec_tokens` value, where exposed | Distribution of the K chosen for steps that scheduled work | Tokens, requests, or time per K |
| Latency | ITL/TPOT, load, version, separate dispatch evidence | Changes that happen together | That SD or CUDA graphs caused a speedup |

## 1. Record what is running

Record the image digest, the vLLM version, the SD method, the drafter, and the
resolved speculative config the server logs at startup. Command-line flags alone
do not show which configuration was applied.

## 2. Check the scrape

Confirm the target is `up`, then read the raw output:

```bash
kubectl port-forward -n ${NAMESPACE} ${MODEL_POD} 8000:8000
curl -s http://localhost:8000/metrics | grep 'vllm:spec_decode'
```

If the series are missing, any of these can be the reason, and they stay open
until ruled out:

- no speculative config resolved;
- engine stats disabled;
- the scrape config dropping them;
- this vLLM version not exposing them.

Check the scrape config before changing deployment manifests.

```promql
absent(vllm:spec_decode_num_drafts_total)
```

## 3. Check that there was request work

Use the throughput and request queries in
[Basic Model Serving](./promql.md#basic-model-serving) for the same pods and
window. HTTP traffic alone does not show that decode steps ran.

## 4. Read drafting and acceptance together

Use the [Speculative Decoding](./promql.md#speculative-decoding) queries for one
scope (model, or pod) and one window:

- **Drafts increasing, acceptance 0**: a real observation. It is not, on its own,
  evidence that the drafter is broken.
- **Drafts flat while requests generate tokens**: no drafting in the window. With
  a static configuration this is unexpected. With a dynamic schedule it can be an
  intentional K=0 (step 5).
- **Acceptance returns no data**: no draft tokens in the window, which is
  undefined, not 0.
- **A ratio is missing but its denominator exists**: one series is not exposed.
  The "Pods with draft tokens but no accepted-token series" query finds these.

`rate()` handles counter resets but not scrape gaps, restarts, or windows shorter
than two scrapes. When comparing runs, record the window and the process
lifetimes separately. The "Counter resets in 5m" query shows restarts.

## 5. Selected K (optional)

Some engines expose `vllm:spec_decode_scheduled_steps_total{num_spec_tokens="K"}`.
It counts scheduler steps that scheduled request work, by the K a dynamic schedule
selected, including 0. It is proposed in
[vllm-project/vllm#54748](https://github.com/vllm-project/vllm/pull/54748) and is
not assumed to be in any vLLM release; check `/metrics`. Read the series in one of
four states:

| State | How to tell | Query |
| --- | --- | --- |
| Unsupported | Base SD counters present, no K series | `count by(model_name, pod, engine) (vllm:spec_decode_num_drafts_total) unless count by(model_name, pod, engine) (vllm:spec_decode_scheduled_steps_total)` |
| Not applicable | K series present but never incremented in this process, and the resolved config shows no dynamic schedule (static K) | `sum by(model_name, pod, engine) (vllm:spec_decode_scheduled_steps_total) == 0` |
| No activity now | Incremented earlier, flat in the window | `(sum by(model_name, pod, engine) (increase(vllm:spec_decode_scheduled_steps_total[5m])) == 0) and on(model_name, pod, engine) (sum by(model_name, pod, engine) (vllm:spec_decode_scheduled_steps_total) > 0)` |
| Active | Increasing in the window | Share per K below |

Share of steps per K, per pod and engine:

```promql
sum by(model_name, pod, engine, num_spec_tokens) (rate(vllm:spec_decode_scheduled_steps_total[5m])) / ignoring(num_spec_tokens) group_left() (sum by(model_name, pod, engine) (rate(vllm:spec_decode_scheduled_steps_total[5m])) > 0)
```

Share of steps per K, per model:

```promql
sum by(model_name, num_spec_tokens) (rate(vllm:spec_decode_scheduled_steps_total[5m])) / ignoring(num_spec_tokens) group_left() (sum by(model_name) (rate(vllm:spec_decode_scheduled_steps_total[5m])) > 0)
```

The shares are weighted by steps, not by requests, tokens, or GPU time.

An increase at K=0 that matches the resolved schedule shows that intentional
no-draft steps happened. It does not show that every request in the window ran
at K=0. It also does not explain every flat draft counter at that time.

Without this counter, whether K=0 was intentional stays undetermined.

## 6. Compare latency separately

Compare ITL/TPOT with load, output length, and version separately from the SD
counters. The counters do not show a speedup, and they do not show which CUDA
graph path ran. Diagnosing a graph fallback needs evidence from the engine's
dispatch side.
