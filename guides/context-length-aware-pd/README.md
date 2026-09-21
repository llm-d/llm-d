# Context-Length-Aware Prefill Routing

A minimal, kustomize-free example: `Qwen/Qwen3-0.6B` served with P/D disaggregation, where
prefill is split into two pools by request context length.

| Deployment      | Role      | `llm-d.ai/context-length-range` |
| --------------- | --------- | ------------------------------- |
| `decode`        | `decode`  | *(none)*                        |
| `prefill-short` | `prefill` | `0-1000`                        |
| `prefill-long`  | `prefill` | `1000-32768`                    |

All three land in one InferencePool (selected by `llm-d.ai/guide: context-length-aware`).
On each request, the EPP runs two scheduling profiles. The `prefill` profile first applies
`prefill-filter` to narrow to the two prefill deployments, then the
[`context-length-aware`](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/scheduling/scorer/contextlengthaware)
plugin — with `enableFiltering: true` — drops whichever prefill pool does not cover the
request's token count. The `decode` profile is untouched: decode pods carry no range label,
and the plugin passes unlabeled pods through.

## Notes on the range label

* Both bounds are parsed with `strconv.Atoi`, so **`Max`, `inf`, and other non-integers do
  not work** — a pod whose label fails to parse is silently dropped from every prefill
  candidate set. Use a concrete integer; here `32768` matches `--max-model-len`.
* The format is exactly `min-max` split on `-`, so negative bounds are also unparseable.
* Ranges are inclusive on both ends. `0-1000` and `1000-32768` overlap at exactly 1000;
  both pods survive filtering and the scorer breaks the tie (tighter range and more
  headroom score higher).
* With `enableFiltering: true`, a request above 32768 tokens matches no prefill pod. That
  request would exceed `--max-model-len` anyway, but the failure surfaces in the EPP rather
  than as a vLLM 400.

## Token counting

Token counts come from the `token-producer` plugin. This example uses its tokenizer-free
`estimate` backend: it packs the request's input bytes into pseudo-tokens at a fixed
**4 bytes per token**. No tokenizer to load, no model download, no network call.

The plugin block in `router.values.yaml` is actually optional. `context-length-aware`
declares `TokenizedPrompt` as a *required* input, and when nothing produces that key the
framework instantiates the default producer for it with nil parameters — which is
`token-producer` on the `estimate` backend. Deleting the block gives you the identical
behavior; it is written out so the choice is visible instead of implicit.

What "imprecise" costs you here:

* For ordinary English prose, 4 bytes/token is close to real BPE behavior, so requests near
  the 1000 boundary may land on either side. The 1k split is a routing heuristic, not a
  correctness constraint — both pools serve the same model with the same `--max-model-len`,
  so a misroute is a performance detail, not a failure.
* Code, JSON, CJK, and other non-prose inputs drift furthest from 4 bytes/token.
* Chat requests estimate over the flattened role + content bytes, so the chat template's own
  tokens are not counted.
* A `/v1/completions` request that sends `prompt` as **token IDs** rather than a string
  bypasses estimation entirely — those are already real tokens and pass straight through.

### Switching to exact counts

If you need the boundary measured against real tokenization, uncomment the
`prefill-tokenizer` Service in `manifest.yaml` and point the producer at vLLM's
`/v1/completions/render` endpoint:

```yaml
- type: token-producer
  parameters:
    modelName: Qwen/Qwen3-0.6B
    vllm:
      url: http://prefill-tokenizer:8000
```

This adds a per-request HTTP call from the EPP to a prefill pod on the hot path (5s default
timeout), and makes scheduling depend on prefill pods being reachable. That is the tradeoff
the `estimate` backend exists to avoid.

## Deploy

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export NAMESPACE="llm-d-context-length-aware"

kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f ${REPO_ROOT}/guides/context-length-aware-pd/manifest.yaml -n ${NAMESPACE}

helm install context-length-aware \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/context-length-aware-pd/router.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

`Qwen/Qwen3-0.6B` is a public model, so no `llm-d-hf-token` secret is required.

## Verify the split

Port-forward the router and send a short and a long prompt:

```bash
kubectl port-forward -n ${NAMESPACE} svc/context-length-aware-epp 8000:80
```

```bash
python3 ${REPO_ROOT}/guides/context-length-aware-pd/verify.py
```

`verify.py` is stdlib-only — no `pip install` — and sends one short prompt and one
~31k-estimated-token prompt.

Then confirm which prefill pod served each request:

```bash
kubectl logs -n ${NAMESPACE} deploy/prefill-short --tail=20
kubectl logs -n ${NAMESPACE} deploy/prefill-long  --tail=20
```

Raising the EPP log level (`--set router.epp.flags.v=4`) surfaces the plugin's own
`Filtered endpoints` / `Scored endpoints` lines with the token count it computed.

### Prompt-length histogram per pod

vLLM publishes a `vllm:request_prompt_tokens` histogram on `/metrics`. Scraping it from each
prefill pod shows the split directly: the short pool's requests all land in the low buckets,
the long pool's only in the high ones.

```bash
for dep in prefill-short prefill-long; do
    pod=$(kubectl get pod -n ${NAMESPACE} -l app=${dep} \
        -o jsonpath='{.items[0].metadata.name}')
    kubectl port-forward -n ${NAMESPACE} pod/${pod} 8001:8000 >/dev/null 2>&1 &
    pf=$!
    sleep 2

    echo "=== ${dep} (${pod})"
    curl -s http://localhost:8001/metrics \
        | grep -E '^vllm:request_prompt_tokens_(bucket|count|sum)'

    kill ${pf}
done
```

Decode serves `/metrics` from vLLM on **8200**, not 8000 — port 8000 there is the routing
proxy. Use `8001:8200` to scrape `deploy/decode`; its histogram sees every request, since
decode handles both pools' traffic.

Histogram buckets are cumulative (`le` is "less than or equal"), so the number of requests in
a bucket is its count minus the previous bucket's. The fastest sanity check is
`_sum / _count`: mean prompt length, which should sit far below 1000 for `prefill-short` and
well above it for `prefill-long`. Note this is vLLM's *real* token count, not the EPP's
4-bytes-per-token estimate, so the two disagree near the boundary — that is the estimator's
error, made visible.
