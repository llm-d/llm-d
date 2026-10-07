# Kustomize Dry-Run CI

The `CI Guide Dry Run` workflow (`.github/workflows/ci-kustomize-dry-run.yaml`) validates that every deployable guide in the `guides/` directory produces valid Kubernetes manifests. It runs on every pull request that touches `guides/**` or the workflow itself.

## What it does

For each included guide overlay it:

1. Runs `kustomize build` to verify the overlay resolves without errors
2. Runs `kubectl apply --dry-run=server` against a temporary kind cluster to verify the rendered manifests are accepted by the Kubernetes API

A GitHub job summary is written at the end showing a pass/fail tree of every overlay tested.

## Excluding a guide

Exclusions are controlled by `guides/.ci-dry-run-exclusions`. Each non-blank, non-comment line is a path relative to `guides/` that will be skipped. Both top-level directories and subpaths are supported:

```
# Skip an entire guide
no-kubernetes-deployment

# Skip a subdirectory within a guide
recipes/modelserver
```

To add a new guide to CI coverage, remove its entry from the file. To exclude one, add it with a comment explaining why.

## Adding CRDs

The workflow installs CRDs for every API group used by the guides before running dry-runs. If you add a guide that uses a new CRD group, add a corresponding install script under `.github/scripts/install-<name>-crds.sh` and reference it in the workflow.
