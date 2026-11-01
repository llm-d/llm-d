# Model-server capability contract and preflight verification for guides

## Summary

llm-d guides implicitly assume things about the model-server image they deploy: does it expose `/v1/chat/completions/render`? Does it emit KV-cache events, and in which format? Does it expose the metrics a router scorer needs? These assumptions are scattered across guide READMEs, image pins, and router plugin configs rather than stated anywhere explicit. When an assumption silently breaks — a feature gate flips upstream, an image pin drifts, a flag gets renamed — the deployment stays `Ready` and keeps serving HTTP 200s while the feature the guide was built around is inert. [#2439](https://github.com/llm-d/llm-d/issues/2439) is a concrete instance: the `render`/`derender`/`generate` endpoints vLLM exposes became opt-in behind `VLLM_ENABLE_SCALE_OUT_ENDPOINTS=1`, the guide's prefill pods never set it, `/v1/*/render` returned 404, and precise-prefix-cache routing ran cache-blind — quietly, with every scorer still answering 200 and a measured ~26% throughput regression.

This proposal introduces a small, engine-neutral **capability contract**: guides declare the model-server capabilities they require (an API, a metric, a telemetry channel) — router plugins may declare their own in a later phase, see Non-Goals — and a lightweight verifier probes the *running* deployment to confirm each declared capability is actually present, rather than trusting a version pin or an image tag. The verifier treats the deployment as the source of truth, not a compatibility database — so it stays correct across vLLM/SGLang releases and upstream flag renames without needing to be updated in lockstep with them.

This proposal (PR 0) defines the capability vocabulary, the runtime-probe contract shape, and the ownership boundary against the related cluster-preflight effort in #2429. It implements nothing. A follow-on PR implements a single capability against a single engine; see Suggested PR sequence.

## Motivation

Today a guide's dependency on a model-server feature is expressed, at best, as prose in a README or a version pin in a Kustomize overlay — never as something that can be checked. That produces a specific and dangerous failure mode: a deployment can be fully `Ready`, pass liveness/readiness probes, and serve requests successfully, while the one feature the guide exists to demonstrate (precise KV-cache routing, in the #2439 case) is silently disabled. Nothing in the current health-check surface distinguishes "healthy and doing what the guide promises" from "healthy and quietly degraded." The guide's own router config already carries a comment acknowledging this exact fragility — [guides/precise-prefix-cache-routing/router/precise-prefix-cache-routing.values.yaml](../guides/precise-prefix-cache-routing/router/precise-prefix-cache-routing.values.yaml) notes render capacity depends on `--enable-scale-out on vLLM 0.30`, a fact that today lives only in a comment, not in anything checkable.

This is distinct from [#2429](https://github.com/llm-d/llm-d/issues/2429), where cluster-prerequisite preflight work is already being actively pursued: `@alexeymoskalev-devops` is implementing a read-only script under `helpers/preflight/` that checks CRDs, RDMA setup, GPU driver versions, and schedulable CPU/GPU, reading its requirements from ["a small per-guide file, or a `requirements:` block in `guide.yaml`"](https://github.com/llm-d/llm-d/issues/2429#issuecomment-5924576061), with a draft PR planned. The two efforts compose into three independent verification layers rather than overlapping:

```
Cluster preflight (#2429)              Model-server capability contract (this proposal)        Functional guide verification
  "Can this cluster run the guide?"      "Does the deployed runtime provide the features          "Does the whole thing actually work?"
  CRDs / GPUs / RDMA / drivers /          the guide assumes?"                                       requests / routing / benchmark
  resources                              APIs / metrics / KV events / connectors / feature gates
```

Because #2429 is already heading toward a `requirements:` block in `guide.yaml`, this proposal deliberately shares that declaration container rather than introducing a second, competing one — see "Where capabilities are declared" below.

### Goals

* Let a guide declare the model-server capabilities it depends on, as engine-neutral capability identifiers (`api.render.chat-completions`, `metric.queue-depth`) rather than implementation details (`VLLM_ENABLE_SCALE_OUT_ENDPOINTS=1`). Router plugins declaring their own capabilities is a later phase — see Non-Goals.
* Verify those capabilities against the **running** deployment — the deployment is the source of truth, not a version/compatibility table that goes stale.
* Detect the `#2439` failure class specifically: a deployment that is `Ready` and serving 200s while a required API is actually absent.
* Define capability identifiers so they are engine-neutral from day one — `metric.queue-depth` names an outcome, not a vLLM metric name — even though the first implementation only ships a vLLM adapter. Other engines are added by adapters later without renaming the capability.
* Share the `requirements:` declaration container with #2429 (`requirements.cluster` for cluster prerequisites, `requirements.modelServer` for this proposal) rather than adding a second per-guide metadata file.
* Ship a PR 0 narrow enough that the only decision maintainers need to make is: *should guides be able to express and runtime-check model-server capabilities, in this shape?*

### Non-Goals (for this proposal)

* Implementing a verifier, a probe, or an adapter. This proposal defines vocabulary and contract shape only; see Suggested PR sequence for where code lands.
* A full taxonomy of every capability in the original concept (NIXL P/D, per-rank metrics, Responses API, experimental connectors, etc.). Out of scope until the base contract has real usage; see Future work.
* Engine adapters for backends other than vLLM. Capability identifiers are engine-neutral; the first adapter (PR 1) targets vLLM only. Additional engines are additive, not a schema change.
* A long-running daemon, admission controller, or Kubernetes operator. The verifier is a CLI invoked on demand (locally, or in a guide's verification lane), not a new always-on component.
* Automatic enforcement/blocking of deployments. The verifier reports pass/fail; gating a guide's install on that result is a follow-on decision for guide owners.
* Semantic validation of KV-event contents (did the event carry the right block hash, etc.) — this proposal's KV-event capabilities are connectivity-only; semantic checks belong to functional guide verification, not the capability contract.
* Plugin-declared requirements auto-aggregated from `EndpointPickerConfig` into an effective requirement set. That needs SIG Router sign-off on a plugin schema change and is deliberately deferred.
* Replacing, blocking on, or restructuring the #2429 preflight script itself. This proposal only asks to share its declaration container.

## Proposal

Two parts, both scoped narrowly in this PR:

1. **A capability vocabulary** — individually checkable, engine-neutral identifiers, picked at one abstraction level so a passing check means exactly one thing:

   * `api.render.chat-completions`, `api.render.completions`, `api.render.messages` — each a distinct, individually-checkable render endpoint, rather than a single `api.render` that could pass because *one* of the three exists while the guide needed a different one.
   * `metric.queue-depth`, `metric.kv-cache-utilization` — logical metric names; the engine-specific adapter maps these to whatever Prometheus family the active engine actually emits.
   * `kv-events.publisher-reachable`, `kv-events.replay-reachable` — transport-level reachability only (see "Shrinking KV-event scope" below).

2. **A runtime-probe contract**: a capability identifier maps to a probe (an OpenAPI path check, a `/metrics` family check, a socket/endpoint reachability check) that runs against a live deployment and returns present/absent plus a reason. The contract says *what* llm-d requires; an adapter decides *how* a given engine currently exposes it. That indirection is what keeps the contract from breaking every time an upstream flag gets renamed — exactly what happened in #2439.

### Where capabilities are declared

This is the question most worth maintainer attention before any code is written. #2429 is already heading toward a `requirements:` block in `guide.yaml`. Rather than add a second, competing per-guide metadata file, this proposal suggests nesting both domains under one container, owned by two different tools:

```yaml
requirements:
  cluster:
    # owned by #2429 — CRDs, RDMA, driver versions, schedulable CPU/GPU
    ...

  modelServer:
    # owned by this proposal
    capabilities:
      - api.render.chat-completions
      - metric.queue-depth
      - metric.kv-cache-utilization
```

The intended model is that `scripts/guide.py` validates/renders both blocks; each has its own checker (`helpers/preflight/` for `cluster`, `helpers/capabilities/` for `modelServer`) with no shared runtime dependency — the cluster checker needs a Kubernetes client, the capability checker needs an HTTP/metrics/socket client against a resolved endpoint, and neither should require the other's dependencies. This is a schema-sharing proposal, not a tool-merging one; see Alternatives for why the tools themselves stay separate. The exact key names (`cluster` / `modelServer`) are a starting proposal, not a demand — the point to settle with the #2429 author and SIG Installation is the shared container, not the specific key spelling.

### User Stories

#### Story 1 — Unit-testing the capability matcher against the #2439 shape

A contributor adds a captured OpenAPI document that lacks `/v1/chat/completions/render` as a fixture in the PR 1 test suite, alongside one that has it. The capability verifier's unit tests assert that the matcher correctly reports `api.render.chat-completions` as absent for the first and present for the second — proving the matching logic handles the #2439 shape, without a live model server or GPU involved. This is PR 1's actual scope: fixture-driven validation of the matcher itself, not yet an automatic check that a real image bump preserves the capability (that requires a live-deployment adapter, which is Story 2 and later PRs).

#### Story 2 — An operator debugging a "it's healthy but slow" report

An operator deploys `precise-prefix-cache-routing`, sees all pods `Ready`, but throughput looks off. They run the verifier against their live deployment. It reports `api.render.chat-completions: FAIL — /v1/chat/completions/render returned 404`, pointing directly at the cause instead of leaving them to bisect scorer behavior.

### Design Details

#### Capabilities, and which PR implements them

This proposal defines the vocabulary below; none of it is implemented here. See Suggested PR sequence for what ships when.

1. **`api.render.chat-completions`** (and siblings `api.render.completions`, `api.render.messages`) — fetch `/openapi.json` from the model server and check for the specific render path. Directly detects the #2439 class of failure. First (and only PR-1) capability.
2. **`metric.queue-depth`, `metric.kv-cache-utilization`** — fetch `/metrics` and check for the required metric family, declared logically rather than by engine-specific name (`vllm:num_requests_waiting` vs. an SGLang equivalent). The vLLM adapter owns the logical-name → vLLM-metric-name mapping, so a guide's declaration doesn't change when a future SGLang adapter is added. Deferred to PR 3 (see below) rather than bundled into the first implementation.
3. **`kv-events.publisher-reachable`, `kv-events.replay-reachable`** — transport reachability only: can a client open the publisher socket, can it reach the replay endpoint. Deferred to PR 4.

**Shrinking KV-event scope:** the original concept for this capability included "subscriber established" and "at least one event observed." Both require generating cache activity against a real workload and a defensible way for a generic external checker to confirm a subscriber is "established" — that's materially more than a cheap connectivity probe, and belongs to functional guide verification (the third layer in the diagram above), not this capability contract. The capability-contract layer should answer "does the interface exist," not "did it participate correctly in a workload" — the latter needs traffic and is a different kind of check with different cost and flakiness characteristics.

**On "replay" as a capability name:** `replay` isn't vLLM-only vocabulary leaking into an otherwise engine-neutral contract — [guides/precise-prefix-cache-routing/README.md:376](../guides/precise-prefix-cache-routing/README.md) already describes a replay buffer as a cross-engine concept ("each pod (vLLM or SGLang) ... retains ... an in-memory replay buffer for index recovery"), and the router subscriber logic in the same guide already treats "a replay endpoint is available" as an optional, checkable property. `kv-events.replay-reachable` names that existing llm-d-level concept, not a vLLM implementation detail.

#### Architecture

A standalone CLI, not a service:

```
helpers/
  capabilities/
    check.py
    schema.yaml
    README.md
```

(Alternatively as a subcommand of existing guide tooling, e.g. `scripts/guide.py capabilities ...`, if maintainers prefer consolidating guide-related CLIs — a decision left to review rather than fixed by this proposal.)

The checker takes endpoint addresses as input and has no Kubernetes client dependency itself — a caller (a shell snippet in a guide's verification steps) is responsible for resolving a Service/pod to a reachable URL, or for pointing it at a captured fixture (PR 1). That keeps the checker usable against Kubernetes, bare metal, or other future deployment targets without modification.

```
guide.yaml: requirements.modelServer.capabilities
    │ declares
    ▼
capability verifier (helpers/capabilities/check.py)
    ├── OpenAPI probe         (api.*)            — PR 1, vLLM adapter
    ├── /metrics probe        (metric.*)         — PR 3, vLLM adapter
    ├── socket/HTTP probe     (kv-events.*)       — PR 4, vLLM adapter
    └── PASS / FAIL per capability, with reason
```

#### Failure reporting

A failed capability should name the capability, the probe result, and (where known) the guide or component that required it — not just a bare `FAIL`:

```
Required capability:
  api.render.chat-completions

Reason:
  /v1/chat/completions/render returned 404

Detected runtime:
  vLLM 0.28.1rc1

This capability is required by:
  guides/precise-prefix-cache-routing
```

The "detected runtime" field is best-effort and may not always be derivable; the capability name and the raw probe result are the only fields the MVP guarantees.

#### Suggested PR sequence

This proposal is PR 0: vocabulary, probe contract shape, and the `requirements.modelServer` / `requirements.cluster` ownership boundary. No code. If accepted, the suggested follow-on sequence (each independently reviewable, each deliberately small) is:

| PR | Scope |
|----|-------|
| 1 | Verifier prototype: `api.render.chat-completions` only, vLLM adapter only, tested against captured/minimal OpenAPI fixtures in the guide's verification lane — no GPU required. |
| 2 | First integration: `guides/precise-prefix-cache-routing` adds `requirements.modelServer.capabilities: [api.render.chat-completions]` to `guide.yaml` and wires the checker into its verification steps. |
| 3 | `metric.queue-depth`, `metric.kv-cache-utilization` — vLLM adapter only. |
| 4 | `kv-events.publisher-reachable`, `kv-events.replay-reachable` — connectivity only. |

Expansion beyond these capabilities (NIXL P/D, per-rank metrics, Responses API, experimental connectors, non-vLLM adapters, plugin-declared requirements aggregated from `EndpointPickerConfig`) is deferred until PRs 1–4 have enough real usage to validate the schema shape — see Alternatives and Non-Goals.

#### Acceptance criteria for the first implementation (PR 1)

1. Running the verifier against a compatible model server (or fixture) exits 0.
2. A missing required capability exits non-zero and names the specific capability, not just "unhealthy."
3. The checker never determines a capability solely from a version string — it probes behavior.
4. Capability identifiers remain engine-neutral even when their adapter implementation is engine-specific — PR 1 ships only a vLLM adapter, but nothing in a guide's `requirements.modelServer.capabilities` list names vLLM.
5. No GPU is required to run the capability-matching unit/CI tests.
6. A deployment (or fixture) where inference works but `/v1/chat/completions/render` is absent is reported as failing `api.render.chat-completions`, reproducing the #2439 scenario.
7. Existing guides are unaffected unless they explicitly add a `requirements.modelServer` declaration — this is opt-in, not a breaking change to any current guide.

#### Future work (explicitly out of scope for this proposal)

* **Non-vLLM adapters**: implementations of the same capability identifiers (`metric.queue-depth`, etc.) for SGLang, TRT-LLM, or other backends, so a guide's declaration stays unchanged across backends.
* **Plugin-declared requirements**: `EndpointPickerConfig` plugins (e.g. `precise-prefix-cache-producer`, `queue-scorer`) declare the capabilities they need directly, and the effective requirement set for a router config is derived from the plugins it loads rather than duplicated by hand in the guide. Needs SIG Router sign-off on the plugin schema change.
* **KV-event functional verification**: "a subscriber is established and an event was observed and is decodable," as a functional-guide-verification capability built on top of the connectivity checks in PR 4, not a replacement for them.
* Richer failure diagnosis (e.g., a "likely cause" heuristic beyond the raw probe result).

## Alternatives

**A static compatibility matrix** ("vLLM 0.29 supports X") was considered and rejected as the primary mechanism. It requires manual maintenance in lockstep with every upstream release and flag change, is exactly the kind of document that caused #2439 (the guide's assumptions were correct for an older vLLM build and silently wrong for `nightly`), and cannot detect a deployment-specific misconfiguration (e.g., the flag exists but wasn't set). A matrix may still be useful as documentation, but it should not be the thing a guide trusts; the running deployment should be.

**Declaring requirements as raw environment variables or CLI flags** (e.g. `VLLM_ENABLE_SCALE_OUT_ENDPOINTS=1`) was considered and rejected for the contract's declaration format, because it couples the guide to today's mechanism for enabling a feature rather than the feature itself — the exact coupling that broke when the flag was introduced. Probing for the resulting behavior (does `/v1/chat/completions/render` respond) is more resistant to upstream renames than asserting a specific flag is set.

**Merging this into the #2429 preflight tool itself** (one script, one checker) was considered and rejected: the two check fundamentally different things at different layers. #2429 asks whether the cluster can run the guide at all (CRDs, drivers, resources) before anything is deployed and needs a Kubernetes API client; this proposal asks whether an already-deployed, already-`Ready` model server actually provides the features the guide depends on, and needs an HTTP/metrics/socket client against a resolved endpoint. Forcing one tool to carry both dependency sets would conflate two failure modes operators need to distinguish ("my cluster can't run this" vs. "my cluster is running this, but a feature is inert"). What *is* shared, per "Where capabilities are declared" above, is the `requirements:` declaration container in `guide.yaml` — sharing the schema without sharing the verifier.

**A long-running daemon or sidecar that continuously monitors capabilities** was considered for a later phase but rejected as premature: it introduces a new always-on component, RBAC surface, and failure mode before the core probe logic and capability vocabulary have been validated by real usage in a few guides.
