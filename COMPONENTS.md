# Components

llm-d is built as a set of components that connect at API boundaries (see the
principles in [PROJECT.md](./PROJECT.md)). This document describes the two
classes of component the project maintains, and how a component in incubation
becomes one of them.

## Core and ecosystem

Every component the project commits to is either core or ecosystem. The
difference is whether it sits on the path that serves an inference request.

A component is **core** if it is on the inference request path - if a well-lit
path cannot serve requests without it, or if it runs inline in the request or
data path, so that a failure or regression there degrades or breaks live
inference. The core set is deliberately small, because everything in it carries
the reliability expectations of production serving.

A component is **ecosystem** if the project maintains it but it is not needed to
serve a request. Design-time and deploy-time tooling, benchmarking and analysis,
simulators, and adjacent services are ecosystem components. They are first-class
parts of llm-d; they simply are not on the hot path.

The line between them is not permanent. An ecosystem component can be promoted to
core if it moves onto the request path and meets the higher bar described below.

## From incubation to core or ecosystem

New and experimental work starts in
[`llm-d-incubation`](https://github.com/llm-d-incubation). Incubation code is
opt-in and isolated, carries no stability promise, and lives there while we
figure out whether it earns a lasting place in the project.

When the project commits to maintaining a component, it moves into the main
[`llm-d`](https://github.com/llm-d) organization and takes on one of the two
roles above. This is a deliberate decision, not something that happens
automatically. The maintainers open a pull request naming the role they are
asking for and showing how the component meets the criteria, and project
maintainers decide by lazy consensus as described in
[PROJECT.md](./PROJECT.md#process), with an explicit sign-off given the weight of
the decision.

Any component the project takes on, core or ecosystem, needs:

- an owning team named in `OWNERS` that commits to maintaining it,
- documentation for users, and a guide or well-lit path where it makes sense,
- working CI, tests, and the standard project linting and sign-off,
- a real use case or a clear gap it fills, and
- a sponsoring project maintainer.

Core components clear a higher bar as well, because they inherit the reliability
expectations of the request path. A core component needs more than one active
maintainer, ideally from more than one organization, since the request path
cannot depend on a single person. It ships on the llm-d release train with
semantic versioning and does not break published APIs (principle #7). And it
holds a production bar for quality: meaningful test coverage, a security policy
and contacts, and a high review bar (principle #5).

A small team, including one with a single maintainer, is fine for an ecosystem
component precisely because it is off the request path. The same component would
need to grow its maintainer base before it could become core. Promoting an
ecosystem component to core follows the same pull-request path, measured against
the core bar.

We review the roster frequently. A component that has stopped meeting
its bar can move to ecosystem or be archived. This is ordinary upkeep, and it
keeps the core set honest.

## Where status is recorded

Graduated components live in the [`llm-d`](https://github.com/llm-d) org and
incubation components in
[`llm-d-incubation`](https://github.com/llm-d-incubation). We tag each graduated
repo with a `core` or `ecosystem` topic, and the main [README](./README.md)
groups components by role so anyone can see at a glance what is on their request
path. The roster below is the source of truth and changes by pull request.

## Roster

### Core

| Component | Purpose |
|-----------|---------|
| `llm-d-router` | Request routing and endpoint picking (EPP / gateway) |
| `llm-d-kv-cache` | Distributed KV cache scheduling and offloading |
| `llm-d-routing-sidecar` | Prefill/decode routing sidecar |
| `llm-d-async` | Asynchronous processor and queue orchestration for the gateway |
| `llm-d-batch-gateway` | OpenAI-compatible batch API and processing engine |
| `llm-d-latency-predictor` | Predicted-latency scoring for live scheduling |
| `llm-d-workload-variant-autoscaler` | KEDA + EPP autoscaling of serving capacity |

### Ecosystem

| Component | Purpose |
|-----------|---------|
| `llm-d-planner` | Capacity planning and configuration at design and deploy time |
| `llm-d-benchmark` | Benchmarking framework and tooling |
| `llm-d-inference-sim` | GPU-free vLLM simulator |
| `llm-d-prism` | Performance analysis for distributed inference |
| `llm-d-inference-cost` | Inference cost analysis |
| `hermes` | Cluster configuration scanning and self-test generation |
| `llm-d-semantic-classifier` | Request semantic classification |
