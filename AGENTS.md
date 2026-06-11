# AGENTS.md

**Do:**
- Edit `README.md` files in guides to fix accuracy, improve clarity, or update version numbers.
- Update Helm values files (`values.yaml`, `values-*.yaml`) in `guides/*/router/` and `guides/*/modelserver/`.
- Update Kustomize overlays (`kustomization.yaml`, patch files) in guide directories.
- Fix env var declarations in `docker/scripts/` shell scripts to satisfy the linter.
- Keep code blocks in guides consistent when environment variables or version pins change.

**Do not:**
- Commit without a DCO sign-off (`git commit -s`).
- Remove or rename environment variables exported in guide `README.md` files without updating every downstream reference in the same guide.
- Introduce new Docker base images or add external dependencies without maintainer discussion.
- Mark experimental guides as stable without approval.
- Break the kustomize dry-run — always check that `kubectl kustomize` succeeds on any overlay you touch.
- Add speculative error handling or defensive abstractions not required by the immediate task.

