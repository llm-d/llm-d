# AGENTS.md

Main `llm-d` repository owning Well-Lit Path guides (`guides/`), Kustomize and Helm deployment compositions, project documentation (`docs/`, published to `llm-d.ai`), architectural proposals (`proposals/`), container image builds (`docker/`), and nightly E2E workflows (`.github/workflows/`). Component source code lives in separate repositories listed in [COMPONENTS.md](COMPONENTS.md) (such as `llm-d-router`, `llm-d-kv-cache`, `llm-d-workload-variant-autoscaler`, and `llm-d-benchmark`).

`make help` lists targets. `make verify` checks generated guide READMEs (`./scripts/guide.py render --recursive --check guides`, or `python3 scripts/guide.py render --recursive --check guides` without `uv`).

## Agent operating rules

**Allowed.** Edit files in the local workspace, run local verification and unit tests, and read repository and GitHub state.

**Ask first.** Pushing commits to any branch, rewriting pushed history, editing `.github/` workflows or `OWNERS` files, or adding external dependencies. Propose the specific change and reason in one message and wait before acting.

**Never, without explicit per-turn authorization.** Public actions under the user's identity: GitHub comments, reviews, reactions, PR or issue state changes, label or reviewer mutations, or posts to Slack or external surfaces. Draft text for the user to post manually.

## Working in the codebase

- **Guide rendering (`scripts/guide.py`).** Guides with a `guide.yaml` render their `README.md` bash fences from that YAML (see [guides/templates/README.md](guides/templates/README.md)). Edit `guide.yaml`, run `python3 scripts/guide.py render <guide-dir>`, and verify with `make verify`; never edit rendered `README.md` bash fences by hand.
- **Guide policy and scope.** Read [guides/GUIDES-POLICY.md](guides/GUIDES-POLICY.md) before adding or restructuring directories under `guides/`. Parameter tweaks belong in an existing guide's tuning section, and composable Kustomize building blocks belong under `guides/recipes/`. Every standalone guide needs an `OWNERS` file; guide paths or non-standalone `kustomization.yaml` bases that [.github/workflows/ci-kustomize-dry-run.yaml](.github/workflows/ci-kustomize-dry-run.yaml) should skip belong in [guides/.ci-dry-run-guide-exclusions](guides/.ci-dry-run-guide-exclusions) with a reason.
- **Website docs (`docs/` and `llm-d.ai`).** `docs/` is synced to `llm-d.ai` and previewed on PRs by [.github/workflows/ci-site-preview.yaml](.github/workflows/ci-site-preview.yaml). Configure sidebar categories and order in [docs/menu-config.json](docs/menu-config.json) rather than adding Docusaurus frontmatter to Markdown files. Register published guides in [docs/well-lit-paths/guides.yaml](docs/well-lit-paths/guides.yaml) and validate with `python3 scripts/guide.py check-manifest docs/well-lit-paths/guides.yaml`.
- **Mirrored community docs and links.** Root [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md), and [SIGS.md](SIGS.md) are mirrored to `llm-d.ai`. Put CI and maintainer reference docs under `.github/`, and link to them with repo-relative paths (`(.github/...)`) rather than `blob/main` URLs so [.github/workflows/link-checker.md](.github/workflows/link-checker.md) passes before merge.
- **Nightly CI and Slack routing sync.** When adding, renaming, or removing a `.github/workflows/nightly-*.yaml` workflow, update [.github/slack-channels.yaml](.github/slack-channels.yaml) and run `python3 scripts/sync-slack-channels.py --fix`. When `nightly-e2e-*.yaml` lanes change, also run `python3 scripts/sync-nightly-matrix.py --fix` to update [release/README.md](release/README.md). Both `--check` hooks run in [.pre-commit-config.yaml](.pre-commit-config.yaml), though `sync-nightly-matrix.py` is a no-op unless `.release-team` exists at the repo root.
- **Agentic workflows.** [.github/workflows/link-checker.md](.github/workflows/link-checker.md), [.github/workflows/typo-checker.md](.github/workflows/typo-checker.md), and [.github/workflows/upstream-monitor.md](.github/workflows/upstream-monitor.md) are executable GitHub Agentic Workflows (`gh-aw`), whereas [.github/workflows/README.md](.github/workflows/README.md) is documentation prose.
- **Tests.** Check Python helpers with `python3 -m pytest scripts/tests/ .github/scripts/nightly-e2e-verification/ docs/operations/validation/smoke-test/tests/ .github/scripts/e2e/async-multitenant/ -v` ([ci-guides-check.yaml](.github/workflows/ci-guides-check.yaml)) and snapshot scripts with `python3 -m unittest discover -t . -s docker/scripts/snapshot` ([ci-pr-checks.yaml](.github/workflows/ci-pr-checks.yaml)). Confirm command output before claiming work is complete.
- **Linters.** Pre-commit runs `shellcheck` and [scripts/lint-envvars.py](scripts/lint-envvars.py) on `docker/scripts/**/*.sh`, `hadolint` and [scripts/lint-dockerfile-envvars.py](scripts/lint-dockerfile-envvars.py) on `docker/Dockerfile.*`, `yamllint` (250 chars max, lowercase booleans), `actionlint`, and `markdownlint` ([.markdownlint.json](.markdownlint.json)); [_typos.toml](_typos.toml) sets spellcheck exceptions. Scripts with `uv run --script` shebangs (`guide.py`, `lint-*.py`) also run via `python3 scripts/...` when `pyyaml` is installed.

## Pull requests and git workflow

- Keep changes minimal and scoped to a single concern; do not fold unrelated cleanups into a PR. Discuss new features, guides, or cross-component changes in an issue or under `proposals/` first per [CONTRIBUTING.md](CONTRIBUTING.md).
- Describe current behavior directly in documentation and comments without temporal framing ("previously", "now", "this PR").
- Sign off every commit for DCO using `git commit -s` (imperative subject around 72 characters, short body focused on why; see [PR_SIGNOFF.md](PR_SIGNOFF.md)). Do not add machine-generated co-author trailers, and do not bypass git hooks (`--no-verify`).
