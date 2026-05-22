# Graduate LLM-D Async to Production

**Authors**: Shimi Bandiel (_Google_)

## Summary

The Async Processor provides a robust, high-performance asynchronous inference pipeline for llm-d, enabling non-blocking processing of LLM requests via a pull-based
  messaging architecture. It has been incubating at llm-d-incubation/llm-d-async (https://github.com/llm-d-incubation/llm-d-async) and is ready to graduate to the main
  llm-d organization.

  The system acts as a bridge between asynchronous messaging backends (GCP Pub/Sub, Redis) and inference gateways. 

  Graduating to llm-d/llm-d-async signals production readiness, aligns the project with the rest of the llm-d ecosystem, and improves discoverability for organizations
  requiring reliable, asynchronous LLM workloads.

  ## Motivation

  The Async Processor has matured through incubation into a resilient, production-grade component:

   - Stable Architectural Foundation: A refined pipeline interface with a unified Gate and MergePolicy model, supporting complex multi-tenant and multi-model routing.
   - Production-Grade Messaging: Support for industry-standard backends including GCP Pub/Sub (native and gated) and Redis (Pub/Sub and Sorted Set for advanced retry logic).
   - Sophisticated Flow Control: Integrated support for local rate-limiting, max-concurrency semaphores, and external capacity signals (Prometheus/Redis) via a pluggable Gate architecture.
   - Resilience & Reliability: Robust exponential backoff with jitter, deadline-aware processing, and graceful shutdown to ensure zero-loss message handling.
   - Security: Full support for TLS/mTLS when communicating with inference gateways and secure credential management for messaging backends.
   - Quality Assurance: Extensive unit and integration test coverage, E2E suites for various transport implementations, and standardized linting and security scanning in CI.
   - Deployment & Observability: Production-ready Helm charts with configurable resource limits, pod-disruption budgets, and comprehensive Prometheus metrics for pipeline health.

  The project is actively maintained and has reached a level of stability where continued incubation no longer reflects its maturity or its critical role in the llm-d stack.

  ## Goals

   - Transfer the repository from llm-d-incubation/llm-d-async to llm-d/llm-d-async.
   - Update the Go module path from github.com/llm-d-incubation/llm-d-async to github.com/llm-d/llm-d-async.
   - Update container image references and registry paths to the llm-d organization.
   - Standardize all documentation, guides, and runbooks to production-quality levels.
   - Formalize the release process as part of the core llm-d release cycle.

  ## Non-Goals

   - Introducing major architectural breaking changes during the migration itself.
   - Replacing existing messaging backends or core interfaces as part of the organizational move.

  ## Proposal

  We propose graduating the Async Processor by transferring the repository from llm-d-incubation to llm-d. This transfer will preserve all historical context—commits,
  issues, pull requests, and stars—while signaling its status as a core, supported component of the llm-d ecosystem.

  ## Design Details

  The graduation process will follow these coordinated steps:

   1. Repository Transfer: Utilize GitHub's "Transfer repository" feature to move llm-d-incubation/llm-d-async to llm-d/llm-d-async. GitHub will automatically provide redirects for the old URL.
   2. Go Module Rename: Perform a mechanical find-and-replace across the codebase to update go.mod and all internal/external imports to the new path
      (github.com/llm-d/llm-d-async).
   3. Container Registry Updates: Update CI/CD pipelines (GitHub Actions) to push images to ghcr.io/llm-d/llm-d-async-* and update default image references in the Helm charts.
   4. Ecosystem Synchronization: Update references in the main llm-d/llm-d documentation, deployment manifests, and any other repositories that integrate with or document
      the Async Processor.
   5. Documentation Polish: Review and refine the docs/ directory to ensure all guides (deployment, scaling, troubleshooting) reflect production best practices.
   6. CI/CD Verification: Run the full suite of integration and E2E tests under the new organization to verify that all automation, permissions, and publishing workflows
      are intact.

  ## Alternatives

   - Keep in Incubation: This would fail to communicate the current maturity of the project to potential adopters and would maintain an unnecessary organizational barrier for contributors already working across other llm-d core projects.
   - Forking: This is undesirable as it would fragment the community, lose historical tracking of issues/PRs, and require manual synchronization of the two codebases during
     the transition.
