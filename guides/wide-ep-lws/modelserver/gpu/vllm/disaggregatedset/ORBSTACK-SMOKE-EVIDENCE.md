# DisaggregatedSet smoke — test evidence

For [llm-d#1485](https://github.com/llm-d/llm-d/issues/1485). Attach to the PR as manual validation; CI covers `kustomize build` and server-side dry-run for the overlays.

---

## What this PR changes (llm-d only)

| Artifact | Role |
|----------|------|
| `legacy-lws/` | Default path: two independent `LeaderWorkerSet` (prefill + decode) — unchanged behavior |
| `disaggregatedset/` | Experimental path: one `DisaggregatedSet` generated from `base/prefill.yaml` + `base/decode.yaml` |
| `disaggregatedset/smoke/` | **Minimal test manifest** — one small `DisaggregatedSet`, no GPUs, no vLLM |
| `scripts/generate-disaggregatedset.py` | Regenerates `disaggregatedset/disaggregatedset.yaml` when base changes |

**This PR does not ship** LWS or DisaggregatedSet controllers. Those are upstream ([kubernetes-sigs/lws](https://github.com/kubernetes-sigs/lws)); the cluster must already have them for a live smoke run.

The local helper used to set up OrbStack is not part of the PR. The PR-scoped artifacts are the Kustomize overlays, generator, CI dry-run support, and this evidence note.

### How to exercise the experimental overlay

The current provider overlays keep using separate prefill and decode `LeaderWorkerSet` resources. The experimental `DisaggregatedSet` overlay is available for early validation of [llm-d#1485](https://github.com/llm-d/llm-d/issues/1485) and requires an LWS build that includes the alpha `DisaggregatedSet` CRD and controller:

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/disaggregatedset
```

The legacy independent-LWS path remains available for rollback and comparison:

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/legacy-lws
```

After changing `base/decode.yaml` or `base/prefill.yaml`, regenerate the `DisaggregatedSet` manifest:

```bash
python3 guides/${GUIDE_NAME}/modelserver/gpu/vllm/scripts/generate-disaggregatedset.py
```

---

## Smoke test — plain language

### Problem

Today, wide-ep-lws deploys **two** `LeaderWorkerSet` objects (prefill + decode). This PR adds an option to deploy **one** `DisaggregatedSet` that owns both roles. Smoke checks that reconciliation and pod labels still match what EPP expects (`llm-d.ai/role`).

### The only workload this PR applies

```bash
kubectl apply -k disaggregatedset/smoke -n llm-d-ds-smoke
```

That applies **one** custom resource:

- **Kind:** `DisaggregatedSet`
- **Name:** `wide-ep-lws-smoke`
- **Source:** `disaggregatedset/smoke/disaggregatedset-smoke.yaml`

You do **not** `kubectl apply` any `LeaderWorkerSet` or Pod YAML — controllers create those.

### What is inside `disaggregatedset-smoke.yaml`

| Field | Value | Why |
|-------|-------|-----|
| `spec.roles[0].name` | `prefill` | Prefill tier |
| `spec.roles[0].replicas` | `1` | One group for smoke |
| `spec.roles[0].leaderWorkerTemplate.size` | `1` | Single worker per group (no multi-worker EP) |
| `spec.roles[0]` container | `registry.k8s.io/pause:3.9` | Placeholder — **not** vLLM; no GPU requests |
| `spec.roles[1]` | `decode` (same shape) | Decode tier |
| Pod labels | `llm-d.ai/role=prefill\|decode`, `llm-d.ai/guide=wide-ep-lws` | Same contract as production base templates |

### What the cluster does after that one `kubectl apply`

```
YOU apply (llm-d)                    CONTROLLERS create (automatic)
─────────────────                    ────────────────────────────────
DisaggregatedSet                     → LeaderWorkerSet  (prefill)
  wide-ep-lws-smoke                  → LeaderWorkerSet  (decode)
                                     → Pod  …-prefill-0  (pause)
                                     → Pod  …-decode-0   (pause)
```

1. **DisaggregatedSet controller** (upstream, `disaggregatedset-system`) watches `DisaggregatedSet` and creates/updates two child `LeaderWorkerSet` resources named like `wide-ep-lws-smoke-<revision>-prefill` and `…-decode`.
2. **LeaderWorkerSet controller** (upstream, `lws-system`) watches those LWS objects and creates one pod per group.

### What smoke proves vs does not prove

| Proves | Does not prove |
|--------|----------------|
| llm-d smoke manifest is valid and reconciles | vLLM starts or serves traffic |
| DS → 2× LWS → 2× Running pods | Production `disaggregatedset/disaggregatedset.yaml` (full GPU/vLLM spec) |
| Pods have `llm-d.ai/role=prefill\|decode` | Gateway, EPP, scheduler, KV cache, P/D routing |
| `kustomize build` for `legacy-lws`, `disaggregatedset`, `smoke` | `gke/` / `coreweave/` overlays (still legacy LWS) |

### Three overlays (do not confuse them)

| Overlay | What you deploy | Used in this smoke? |
|---------|-----------------|---------------------|
| `legacy-lws/` | 2× `LeaderWorkerSet` (current default) | No — different code path |
| `disaggregatedset/` | 1× `DisaggregatedSet` (full vLLM from `base/`) | No — needs GPUs + HF secret |
| `disaggregatedset/smoke/` | 1× `DisaggregatedSet` (pause only) | **Yes** |

---

## Cluster prerequisites (upstream — not in this PR)

Live smoke needs a cluster that already has:

- [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) (for this run, release `v0.8.0`)
- DisaggregatedSet CRD + controller from [kubernetes-sigs/lws](https://github.com/kubernetes-sigs/lws) commit `f88b79a53a0e8d810d114387a89454d23ea299bb`

DisaggregatedSet is alpha and is not part of the `v0.8.0` LWS release manifest. CI installs CRDs only (no controllers) from the same pinned LWS commit:

```bash
.github/scripts/install-disaggregatedset-crds.sh
```

---

## Commands run (smoke workload + verify)

```bash
kubectl create namespace llm-d-ds-smoke --dry-run=client -o yaml | kubectl apply -f -

# llm-d: single DisaggregatedSet CR
kubectl apply -k disaggregatedset/smoke -n llm-d-ds-smoke

kubectl wait --for=condition=Available disaggregatedset/wide-ep-lws-smoke -n llm-d-ds-smoke --timeout=180s
kubectl get disaggregatedset,leaderworkerset,pods -n llm-d-ds-smoke
kubectl get pods -n llm-d-ds-smoke -l 'llm-d.ai/guide=wide-ep-lws' --show-labels
```

Static (no cluster):

```bash
kustomize build legacy-lws
kustomize build disaggregatedset
kustomize build disaggregatedset/smoke
```

Cleanup: `kubectl delete namespace llm-d-ds-smoke`

---

## Results summary — OrbStack, 2026-05-29

Cluster: single-node OrbStack (`kubectl` context `orbstack`).

Controller/CRD provenance for this run:

- LWS controller: upstream release `v0.8.0`
- DisaggregatedSet CRD/controller: local upstream LWS checkout at `f88b79a53a0e8d810d114387a89454d23ea299bb`
- llm-d manifests: this PR working tree

```text
$ kubectl get disaggregatedset,leaderworkerset,pods -n llm-d-ds-smoke

disaggregatedset/wide-ep-lws-smoke

leaderworkerset/wide-ep-lws-smoke-9814f73a-prefill   READY 1/1
leaderworkerset/wide-ep-lws-smoke-9814f73a-decode    READY 1/1

pod/wide-ep-lws-smoke-9814f73a-prefill-0   1/1 Running   pause:3.9   llm-d.ai/role=prefill
pod/wide-ep-lws-smoke-9814f73a-decode-0    1/1 Running   pause:3.9   llm-d.ai/role=decode
```

`<revision>` in LWS names is controller-generated and changes when spec changes.

**Pass:** 1 DS → 2 LWS → 2 pods Running; correct `llm-d.ai/role`; all three overlays `kustomize build` OK.

---

## Local repro note (optional, not for PR review)

OrbStack required building the upstream DisaggregatedSet controller image locally (`controller:latest`) because the upstream alpha manifest uses a non-published controller image. That is an environment workaround, not part of the llm-d change set.
