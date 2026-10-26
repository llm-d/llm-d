# Diagnose Speculative Decoding

Use this page when vLLM's speculative decoding (SD) counters are flat, missing, or
show an unexpected acceptance. It covers vLLM only; SGLang is not covered yet. The
metrics are described in [Model Server Metrics](./model-server-metrics.md#vllm-speculative-decoding-metrics)
and the queries are in the [PromQL Query Reference](./promql.md#speculative-decoding).

Each step below records one kind of evidence. A query result is an observation,
not a cause. A missing series is recorded as unknown, not filled in as 0.

## What each signal can and cannot show

| Signal | Evidence | Shows | Does not show |
| --- | --- | --- | --- |
| Configuration | Image digest, vLLM version, resolved speculative config in the server log | The SD config the running engine reports | That drafting happened, or output correctness |
| Collection | The server's raw `/metrics` and what Prometheus stored for the same target | Where a series stops appearing | That SD is off, from a missing series alone |
| Drafting | Increase of drafts and draft tokens in the window | Drafting recorded in that scope | That every request or step drafted |
| Acceptance | Accepted over draft tokens, same engines and window | Share of recorded draft tokens accepted | Health, correctness, or end-to-end speedup |
| Selected K (optional) | Increase per `num_spec_tokens` value of the proposed upstream metric | Distribution of the K chosen for steps that scheduled work | Tokens, requests, or time per K |
| Latency | ITL/TPOT, load, version, separate dispatch evidence | Changes that happen together | That SD or CUDA graphs caused a speedup |

## 1. Record what is running

Record the image digest, the vLLM version, the SD method, the drafter, and the
resolved speculative config the server logs at startup. Command-line flags alone
do not show which configuration was applied.

## 2. Compare the raw output with what Prometheus stored

Read the server's own output. Run the port-forward in one terminal and `curl` in
another:

```bash
# Terminal 1
kubectl port-forward -n ${NAMESPACE} ${MODEL_POD} 8000:8000

# Terminal 2
curl -s http://localhost:8000/metrics | grep 'vllm:spec_decode'
```

Then check what Prometheus has for the same target. Keep the selector: without
it, any other target's series makes the result empty.

```promql
absent(vllm:spec_decode_num_drafts_total{namespace="NAMESPACE", pod="POD"})
```

- **In the raw output but not in Prometheus**: look at the scrape target,
  relabelling, and the query's selectors.
- **Not in the raw output**: look at the engine side, i.e. whether a speculative
  config was resolved, whether engine stats are enabled, and whether this vLLM
  version exposes the counters. Scrape configuration does not remove series from
  the server's own output.

Each of these stays a candidate until it is ruled out.

## 3. Check that there was request work

Use the throughput and request queries in
[Basic Model Serving](./promql.md#basic-model-serving) for the same pods and
window. HTTP traffic alone does not show that decode steps ran.

## 4. Read drafting and acceptance together

Use the [Speculative Decoding](./promql.md#speculative-decoding) queries for one
scope and one window. Run the per-engine coverage check before reading a
model-level ratio.

- **Draft tokens increasing, acceptance 0**: a real observation. It is not, on
  its own, evidence that the drafter is broken.
- **No observed increase in drafts while requests generate tokens**: the
  counters recorded no drafting in the window. Candidates:
  - no drafting happened;
  - stats were not being recorded;
  - samples are missing.

  With a dynamic schedule it can also be an intentional K=0 (step 5).
- **Acceptance returns no data**: undefined. Check separately whether the
  draft-token rate is positive, and whether both series exist for the same
  engine.
- **A ratio is missing while its denominator series exists**: candidates:
  - the numerator series is not exposed or not collected;
  - the denominator rate is 0;
  - there are too few samples in the window.

  Check the denominator rate and the numerator series one at a time. The
  "Engines with draft tokens but no accepted-token series" query covers the
  numerator series.

`rate()` adjusts for observed counter resets and extrapolates across the window.
Missing samples and process replacement still limit interpretation. When
comparing runs, record the window and the process lifetimes separately. The
"Observed counter resets in 5m" query gives a hint of restarts, not a count.

## 5. Selected K (optional)

The proposed upstream metric
`vllm:spec_decode_scheduled_steps_total{num_spec_tokens="K"}`
([vllm-project/vllm#54748](https://github.com/vllm-project/vllm/pull/54748), not
merged) counts scheduler steps that scheduled request work, by the K a dynamic
schedule selected, including 0. Do not assume it is in any vLLM release; check
`/metrics`.

The queries below report what is observed. Whether the metric is supported, and
whether it applies (static K versus a dynamic schedule), is confirmed from the
installed version and the resolved config, not from the queries.

| Observed | Query | Check next |
| --- | --- | --- |
| K series not observed, base SD counters present | `count by(model_name, namespace, pod, engine) (vllm:spec_decode_num_drafts_total) unless count by(model_name, namespace, pod, engine) (vllm:spec_decode_scheduled_steps_total)` | Whether the installed version has the metric; engine stats, scrape, and selectors (step 2) |
| K series present, all 0 | `sum by(model_name, namespace, pod, engine) (vllm:spec_decode_scheduled_steps_total) == 0` | Whether the resolved config is static or dynamic, and whether there was work since the process started. A static config and a new process with a dynamic schedule look the same here |
| Positive before, no increase in the window | `(sum by(model_name, namespace, pod, engine) (increase(vllm:spec_decode_scheduled_steps_total[5m])) == 0) and on(model_name, namespace, pod, engine) (sum by(model_name, namespace, pod, engine) (vllm:spec_decode_scheduled_steps_total) > 0)` | Enough samples in the window, resets and process lifetime, current request work |
| Increasing in the window | Share per K below | The distribution below |

Share of steps per K, per engine:

```promql
sum by(model_name, namespace, pod, engine, num_spec_tokens) (rate(vllm:spec_decode_scheduled_steps_total[5m])) / ignoring(num_spec_tokens) group_left() (sum by(model_name, namespace, pod, engine) (rate(vllm:spec_decode_scheduled_steps_total[5m])) > 0)
```

Share of steps per K, per model:

```promql
sum by(model_name, num_spec_tokens) (rate(vllm:spec_decode_scheduled_steps_total[5m])) / ignoring(num_spec_tokens) group_left() (sum by(model_name) (rate(vllm:spec_decode_scheduled_steps_total[5m])) > 0)
```

The shares are weighted by steps, not by requests, tokens, or GPU time.

An increase at K=0 that matches the resolved schedule shows that intentional
no-draft steps were recorded. It does not show that every request in the window
ran at K=0. It also does not explain every flat draft counter at that time.

Without this metric, whether K=0 was intentional stays undetermined.

## 6. Compare latency separately

Compare ITL/TPOT with load, output length, and version separately from the SD
counters. The counters do not show a speedup, and they do not show which CUDA
graph path ran. Diagnosing a graph fallback needs evidence from the engine's
dispatch side.
