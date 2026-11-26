# Area and `docs-only` Label Taxonomy

`area/*` labels route issues and pull requests to the owning Special Interest Groups ([SIGS.md](../SIGS.md)) and component maintainers. Each `area/*` label maps to one or more repository paths, and PRs that modify only documentation or diagram assets also receive a synced `docs-only` label.

[`.github/labeler.yml`](labeler.yml) is the canonical source for additive `area/*` path globs, and [`.github/labeler-sync.yml`](labeler-sync.yml) defines the synced `docs-only` rule.

A change can span multiple areas. Every matching `area/*` label applies additively rather than excluding others:

- A prose edit to `guides/flow-control/README.md` receives `area/guides`, `area/router`, `area/flowcontrol`, `area/docs`, and `docs-only`.
- If a later commit on the same PR modifies `guides/flow-control/guide.yaml`, the workflow removes `docs-only` automatically while retaining the `area/*` labels.
- An edit to `.github/area_taxonomy.md` or `.github/workflows/README.md` receives `area/ci` (everything under `.github/`), `area/docs`, and `docs-only`.

## How Labels Are Applied

Labels are applied through four mechanisms. The workflows only apply labels that already exist in the repository and log a warning for any that do not, so create the label before adding a new area to [`.github/labeler.yml`](labeler.yml).

- **Path auto-labeling on PRs**: [`.github/workflows/pr-area-labeler.yaml`](workflows/pr-area-labeler.yaml) adds matching `area/*` labels and adds or removes `docs-only` when a PR is opened, edited, updated, or reopened. [`.github/scripts/area_labels.py`](scripts/area_labels.py) evaluates the globs.
- **PR and issue body directives**: Add `/area <name>` on its own line in a PR or newly opened issue body (see [`.github/workflows/pr-area-labeler.yaml`](workflows/pr-area-labeler.yaml) and [`.github/workflows/issue-area-label.yaml`](workflows/issue-area-label.yaml)).
  Directives are additive so editing a PR body does not wipe path-derived labels; use `/remove-area <name>` in a comment to remove a label. On PRs, a label that still matches a changed path or a `/area` line in the body is re-applied on the next push or description edit, so remove the directive from the body as well. Directives inside HTML comments or fenced code blocks are ignored.
- **Issue Form fields**: [`.github/workflows/issue-area-label.yaml`](workflows/issue-area-label.yaml) maps the `Area` / `Feature Area` dropdown in [`.github/ISSUE_TEMPLATE/bug.yml`](ISSUE_TEMPLATE/bug.yml) and [`.github/ISSUE_TEMPLATE/feature-request.yml`](ISSUE_TEMPLATE/feature-request.yml) as well as the `Implementation Plan` checkboxes in [`.github/ISSUE_TEMPLATE/hardware-platform-support.yml`](ISSUE_TEMPLATE/hardware-platform-support.yml) to the corresponding `area/*` labels when an issue is opened.
- **Comment commands**: Comment `/area <name>` or `/remove-area <name>` on any issue or PR via [`.github/workflows/prow-github.yaml`](workflows/prow-github.yaml) and [`.prowlabels.yaml`](../.prowlabels.yaml).

## Structural Areas

The structural `area/*` labels classify which repository layer a change touches:

| Label | Scope | Issues & PRs | Slack |
| ----- | ----- | ------------ | ----- |
| `area/guides` | Well-lit path, model, and operations deployment guides (`guides/**`) | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fguides) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fguides) | `#sig-docs`, `#llm-d-dev` |
| `area/docs` | Website documentation, architecture docs, contributor docs, diagrams, and markdown READMEs | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fdocs) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fdocs) | `#sig-docs` |
| `area/proposals` | Design proposals and project RFCs (`proposals/**`) | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fproposals) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fproposals) | `#llm-d-dev` |
| `area/infra` | Shared K8s/Gateway recipes, cloud providers, RDMA/storage infra, lifecycle, and helpers | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Finfra) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Finfra) | `#sig-installation` |
| `area/docker` | Container image Dockerfiles, build scripts, constraints, and upstream patches | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fdocker) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fdocker) | `#llm-d-dev`, `#sig-installation` |
| `area/ci` | GitHub Actions workflows, E2E harnesses, release matrices, linters, and repo scripts | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fci) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fci) | `#llm-d-dev` |

## SIG and Domain Areas

The domain `area/*` labels route guides, documentation, proposals, and issues to the responsible SIGs:

| Label | Focus Area | Issues & PRs | Slack |
| ----- | ---------- | ------------ | ----- |
| `area/router` | Request routing, optimized baseline, predicted latency, multi-model routing, and API references | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Frouter) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Frouter) | `#sig-router`, `#sig-inference-payload-processor` |
| `area/flowcontrol` | Request queuing, concurrency management, and multi-tenant fairness (nested with `area/router`) | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fflowcontrol) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fflowcontrol) | `#sig-router` |
| `area/kvcache` | Precise prefix-cache routing, tiered KV offloading, CephFS, and P2P KV sharing | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fkvcache) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fkvcache) | `#sig-kv-disaggregation` |
| `area/disaggregation` | Prefill/Decode disaggregation, Coordinator E/P/D topologies, and Wide Expert Parallelism | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fdisaggregation) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fdisaggregation) | `#sig-pd-disaggregation` |
| `area/autoscaling` | KEDA/WVA autoscaling, fast model actuation, pod snapshots, and P2P weight loading | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fautoscaling) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fautoscaling) | `#sig-autoscaling` |
| `area/observability` | Prometheus, Grafana dashboards, alerting, distributed tracing, and runtime ML-BOM inventory | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fobservability) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fobservability) | `#sig-observability` |
| `area/batch` | Batch gateway and asynchronous queue-based inference | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fbatch) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fbatch) | `#sig-batch-inference` |
| `area/agentic` | Stateful OpenAI Responses API and multi-turn priority fairness | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fagentic) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fagentic) | `#sig-agentic-inference` |
| `area/multimodal` | Multimodal and Omni model serving guides and topologies | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fmultimodal) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fmultimodal) | `#sig-router`, `#sig-pd-disaggregation` |
| `area/models` | Benchmarked SOTA model serving recipes (`guides/models/**`) | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fmodels) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fmodels) | `#llm-d-dev`, `#sig-benchmarking` |
| `area/rl` | Reinforcement learning rollout routing and integration | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Frl) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Frl) | `#sig-rl` |
| `area/benchmarking` | Performance validation, smoke tests, interactive benchmarking, and perf analysis | [issues](https://github.com/llm-d/llm-d/issues?q=is%3Aopen+is%3Aissue+label%3Aarea%2Fbenchmarking) / [PRs](https://github.com/llm-d/llm-d/pulls?q=is%3Aopen+is%3Apr+label%3Aarea%2Fbenchmarking) | `#sig-benchmarking` |

## Synced `docs-only` Label

`docs-only` applies automatically when every changed file in a PR matches documentation prose (including `.github/area_taxonomy.md`, `.github/workflows/README.md`, and `.github/scripts/**/README.md`), diagram assets (`.png`, `.svg`, `.gif`), `llm-d.ai` navigation metadata (`docs/menu-config.json`, `docs/well-lit-paths/guides.yaml`), license files (`DCO`, `LICENSE`), or prose linter configurations (`.markdownlint.json`, `_typos.toml`).

A PR does not receive `docs-only` if it touches any of the following:

- `.github/workflows/*.md` other than `README.md`. These are agentic workflow definitions that CI runs, not documentation.
- `.github/ISSUE_TEMPLATE/*`
- `OWNERS` files
- `release/README.md`, which holds the generated nightly matrices
- Shell scripts, Python scripts, and Dockerfiles
- Kubernetes, Helm, and Kustomize YAML manifests
