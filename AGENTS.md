# AGENTS.md

`llm-d` integration repository: Well-Lit Path guides (`guides/`), Kustomize and Helm deployment compositions, project documentation (`docs/`, published with the root community docs to `llm-d.ai`), proposals (`proposals/`), container image builds (`docker/`), and nightly E2E workflows (`.github/workflows/`).

`make help` lists targets. `make verify` checks generated guide README sections (`./scripts/guide.py render --recursive --check guides`, or `python3 scripts/guide.py render --recursive --check guides` without `uv`).

## Agent operating rules

**Allowed.** Edit code, run `make` targets, read the codebase and GitHub state.

**Ask first.** Pushing commits to any branch (including feature branches), rewriting pushed history, edits under `.github/` or to `OWNERS`, dependency upgrades. When asking, propose the specific change and the reason in one message; do not start the work in the same turn.

**Never, without explicit per-turn authorization.** Public actions under the user's identity: GitHub comments, reviews, reactions, PR state changes, label or reviewer assignment, posts to Slack or any external surface. Draft such replies as quoted text for the user to send. Authorization is per-action and does not carry between actions or to sub-agents.

## Before you write code

- Read [CONTRIBUTING.md (Before You Write Code)](CONTRIBUTING.md#before-you-write-code): discuss new features and guides first, avoid speculative hardening, and treat the human submitter as author of record. When a new guide or feature has no tracking issue, ask the user to file or link one before writing it.
- Component source code lives in the repositories listed in [COMPONENTS.md](COMPONENTS.md). When a bug or gap is in a component, report and fix it in that component repository rather than working around it in a guide.
- Read [guides/GUIDES-POLICY.md](guides/GUIDES-POLICY.md) before adding or restructuring anything under `guides/`, including baseline requirements ([section 4](guides/GUIDES-POLICY.md#4-baseline-requirements-for-every-guide)) and foundation vs. model guide composition.

## Working in the codebase

- In guides with a `guide.yaml`, content between `<!-- guide:<path> start/end -->` markers in `README.md` is generated (`python3 scripts/guide.py render <guide-dir>`); edit `guide.yaml` for marked blocks, while prose and unmarked blocks outside markers stay in `README.md`.
- Register published guides in [docs/well-lit-paths/guides.yaml](docs/well-lit-paths/guides.yaml) (`python3 scripts/guide.py check-manifest docs/well-lit-paths/guides.yaml`) and configure `docs/` sidebar order in [docs/menu-config.json](docs/menu-config.json) per [CONTRIBUTING.md](CONTRIBUTING.md#documentation).
- Add non-standalone `kustomization.yaml` bases or docs-only guide directories to [guides/.ci-dry-run-guide-exclusions](guides/.ci-dry-run-guide-exclusions) with a comment explaining why.
- Follow the README runner rules in [guides/templates/README.md](guides/templates/README.md): keep placeholders out of unskipped fences, wrap optional or alternative commands in `llm-d-cicd:skip`, do not set variables inside a skipped block that later steps read, and remember steps share one shell (so `EXIT` traps do not fire per step).
- Verify documentation claims against the guide's own Helm/Kustomize values and pinned component versions before naming a metric, route, or flag, and write "verified" only for configurations that were actually run.
- Compose shared overlays from `guides/recipes/` and foundation guides rather than duplicating full component defaults.
- Place shared Kustomize building blocks under `guides/recipes/` and provider or cluster setup under `docs/infrastructure/`, and link existing reference docs instead of repeating them.
- Use publicly pullable container images only; if the image check ([ci-verify-component-images.yaml](.github/workflows/ci-verify-component-images.yaml)) fails, compare the reported image against your diff before changing unrelated manifests.
- When adding or renaming a `.github/workflows/nightly-*.yaml` workflow, update [.github/slack-channels.yaml](.github/slack-channels.yaml) and run `python3 scripts/sync-slack-channels.py --fix`.
- Pre-commit hooks live in [.pre-commit-config.yaml](.pre-commit-config.yaml). After changing guides or scripts, run `python3 scripts/guide.py render --recursive --check guides` and `python3 -m pytest scripts/tests/ .github/scripts/nightly-e2e-verification/ docs/operations/validation/smoke-test/tests/ .github/scripts/e2e/async-multitenant/ -v` (plus `python3 -m unittest discover -t . -s docker/scripts/snapshot` when touching `docker/scripts/snapshot/`), and confirm command output before claiming work is complete.

## Prose

- Docs and comments describe the current state on its own terms. No "previously", "now", "recently", "renamed from", "added to fix", "this PR", "see above", or other temporal, deictic, or conversational framing. A reader with no context for the change must still understand the text.
- State each fact once, in its canonical location. Do not duplicate across guides, prose, tables, inline comments, and examples.

## Pull requests and commits

- Follow [CONTRIBUTING.md (Commit and Pull Request Style)](CONTRIBUTING.md#commit-and-pull-request-style) and keep each PR scoped to a single concern.
- Rebase onto `main` before opening a PR so moved paths and merged changes are picked up cleanly.
- Sign off every commit for DCO with `-s` and sign commits with `-S` (`git commit -s -S`); see [PR_SIGNOFF.md](PR_SIGNOFF.md) (`Check Signed Commits` fails without `-S`).
