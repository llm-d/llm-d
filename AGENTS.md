# Agent Contributor Guide

This file gives coding agents a fast map of the llm-d repository. Follow the
project guidance in [CONTRIBUTING.md](CONTRIBUTING.md) first; this file points
agents to the right local docs and checks before opening a PR.

## Start Here

- Read [README.md](README.md) for the project scope and core architecture.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) before making changes. Keep PRs scoped,
  describe the problem clearly, and sign commits with `git commit -s`.
- Check [OWNERS](OWNERS) files near the files you edit so the right maintainers
  can review the change.
- For large user-visible behavior changes, new public APIs, new components, or
  new testing methodologies, open or reference an issue/proposal before
  implementation. Use [proposals/PROPOSAL_TEMPLATE.md](proposals/PROPOSAL_TEMPLATE.md)
  when a project proposal is required.

## Repository Map

- `docs/` contains architecture, API, operations, and infrastructure docs.
- `guides/` contains well-lit path guides and their Kubernetes/Kustomize/Helm
  resources.
- `guides/recipes/` contains shared building blocks reused by multiple guides.
- `.github/workflows/` contains CI, nightly guide tests, and status workflows.
- `helpers/` contains contributor and user helper docs for benchmarks, smoke
  tests, clients, and Hugging Face token setup.

## Well-Lit Path Changes

- Use [guides/README.md](guides/README.md) as the index for current well-lit path
  categories and guide conventions.
- Source shared environment values from `guides/env.sh` in guide commands rather
  than hardcoding chart URLs, chart versions, or common paths.
- Prefer shared recipe components under `guides/recipes/` for model server
  images, router values, and common Kustomize resources. If a guide needs a
  temporary image override, include a `TODO(#issue)` comment that tracks removal.
- Keep guide README commands and the matching `router/`, `modelserver/`, and
  monitoring manifests in sync. If you change labels, selectors, release names,
  or namespaces in one place, search the guide for the paired references.
- For new well-lit path guides, add or update the relevant `OWNERS` file and
  cross-link the guide from [guides/README.md](guides/README.md) and the docs
  index where appropriate.

## Router And Docs Sync

- Router behavior and plugins often live in
  [llm-d/llm-d-router](https://github.com/llm-d/llm-d-router). When updating
  plugin docs or examples here, verify names, flags, and config shapes against
  the router repository.
- Start router architecture edits from
  [docs/architecture/core/router/README.md](docs/architecture/core/router/README.md)
  and [docs/architecture/core/router/epp/README.md](docs/architecture/core/router/epp/README.md).
- Standalone and Gateway mode docs are in
  [docs/architecture/core/router/proxy.md](docs/architecture/core/router/proxy.md).
  Use those anchors instead of placeholder links.

## CI And Nightly Workflows

- Read [.github/workflows/README.md](.github/workflows/README.md) before adding
  or changing nightly benchmark workflows.
- Nightly guide testing uses paired workflows: one `nightly-e2e-*` workflow and
  one `consolidate-status-*` workflow. Keep names aligned with the convention
  documented in the workflow README.
- New workflow behavior that depends on external infrastructure should point to
  the reusable workflows in `llm-d-infra` and avoid duplicating large scripts in
  this repository.

## Validation Checklist

- For Markdown-only changes, run the local formatter or linter if available and
  check links touched by the PR.
- For Helm values changes, render the affected chart with `helm template` using
  the same `-f` files shown in the guide.
- For Kustomize changes, run `kustomize build <overlay>` or
  `kubectl kustomize <overlay>` for the affected overlay.
- For guide command changes, verify the commands still use variables from
  `guides/env.sh` and match the target deployment mode.
- Do not add new external dependencies, broad refactors, or speculative
  hardening unless the linked issue/proposal asks for them.
