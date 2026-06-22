# Graduate Batch Gateway to Production

**Authors**: Lior Aronovich (_Red Hat_)

## Summary

The Batch Gateway provides an OpenAI-compatible batch inference API for llm-d, enabling batch and interactive workloads to run efficiently on shared GPU infrastructure. It has been incubating at [llm-d-incubation/batch-gateway](https://github.com/llm-d-incubation/batch-gateway) and is ready to graduate to the main `llm-d` organization.

The system consists of an API server and a batch processor, backed by PostgreSQL for job and file metadata, Redis or Valkey for priority queuing, event coordination, and status caching, and S3-compatible object storage or filesystem for input and output files.  
The system supports SLO-based prioritization, multi-model and multi-endpoint dispatch, graceful shutdown, job recovery, and integration with the llm-d Router and flow control. Integration with the llm-d async processor is forthcoming, and additional integrations with llm-d components are on the roadmap.

Graduating to `llm-d/llm-d-batch-gateway` signals production readiness, aligns the project with the rest of the llm-d ecosystem, and improves discoverability for adopters and contributors.

## Motivation

The Batch Gateway has matured through incubation into a feature-complete, tested, and documented system:

- **Stable API surface**: Full OpenAI Batch API compatibility (`/v1/batches`, `/v1/files`)
- **Multi-tenancy**: Tenant-scoped job and file isolation with configurable authentication
- **Production-grade processing**: Priority scheduling with SLO-aware ordering, multi-model and multi-endpoint dispatch, flow control
- **Resilience**: Graceful shutdown with job re-enqueue, startup crash recovery, orphan job detection and recovery
- **Security**: TLS support, security response headers, security scanning in CI
- **Deployment**: Helm chart with configurable replicas, resource limits, and dependency wiring, tested on Kubernetes and OpenShift
- **Quality**: Comprehensive unit tests, integration tests, E2E test suite covering the full job lifecycle and storage backends (running on every merge and daily), pre-commit checks (e.g. lint, vet, security scan), CI via GitHub Actions
- **Documentation**: Architecture design docs, deployment and operations guides, development guide

The project is actively maintained and has reached the point where continued incubation no longer reflects its maturity.

### Goals

- Transfer the repository from `llm-d-incubation/batch-gateway` to `llm-d/llm-d-batch-gateway`
- Update the Go module path from `github.com/llm-d-incubation/batch-gateway` to `github.com/llm-d/llm-d-batch-gateway`
- Update container image references to the `llm-d` organization
- Bring the deployment guide and user documentation to production quality
- Release as part of the llm-d release process

### Non-Goals

- Architectural changes or new features as part of the migration
- Rewriting existing code beyond what is required for the module path rename

## Proposal

We propose graduating the Batch Gateway by transferring the repository from `llm-d-incubation` to `llm-d`. GitHub's repository transfer preserves commit history, issues, pull requests, and stars. GitHub automatically redirects the old URL to the new one.

## Design Details

The migration consists of the following steps:

1. **Repository transfer**: Use GitHub's "Transfer repository" feature to move `llm-d-incubation/batch-gateway` to `llm-d/llm-d-batch-gateway`. This preserves all history, issues, PRs, branches, and releases. GitHub sets up automatic redirects from the old URL.

2. **Go module path rename**: Update `go.mod` and all import paths from `github.com/llm-d-incubation/batch-gateway` to `github.com/llm-d/llm-d-batch-gateway`. This is a mechanical find-and-replace across the codebase.

3. **Container image registry**: Update image references in the Helm chart, CI workflows, and documentation from `ghcr.io/llm-d-incubation/batch-gateway-*` to `ghcr.io/llm-d/llm-d-batch-gateway-*`.

4. **Downstream dependency updates**: Update references in other llm-d repositories that depend on or reference the Batch Gateway (e.g., `llm-d/llm-d` documentation, deployment guides).

5. **Documentation**: Ensure the deployment guide and user-facing documentation meet production quality standards, including prerequisites, configuration reference, and operational runbook.

6. **CI/CD**: Verify that GitHub Actions workflows, release automation, and Helm chart publishing work correctly under the new organization.

## Alternatives

- **Keep in incubation**: The project has outgrown incubation. Remaining in `llm-d-incubation` creates confusion about its production readiness and makes it harder for users to discover alongside the rest of the llm-d stack.

- **Fork instead of transfer**: Forking would lose issue and PR history and create a disconnected copy. GitHub's transfer feature is the standard approach and preserves everything.
