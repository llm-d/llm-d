# Rollout Guides

This directory contains step-by-step guides for performing incremental deployment operations in llm-d, gradually introducing new versions of inference infrastructure with minimal service disruption.

## Guides

### [Blue-Green Update](./blue-green-update.md)

Use HTTPRoute traffic splitting to shift traffic incrementally between two InferencePool instances. Supports node/accelerator hardware upgrades, base model rollouts, and model server framework updates. Requires llm-d router gateway mode.

### [LoRA Adapter Rollout](./adapter-rollout.md)

Use `InferenceModelRewrite` to map stable model names to specific adapter versions and split traffic across them. No infrastructure changes required. Works in both standalone and gateway modes.

## General Rollout Pattern

All rollout guides follow a similar pattern:

1. **Deploy new infrastructure** - Create the new version alongside the existing one
2. **Configure traffic splitting** - Gradually shift traffic to the new version (e.g., 10% → 50% → 100%)
3. **Monitor and validate** - Verify the new version performs correctly at each stage
4. **Complete rollout** - Direct 100% of traffic to the new version
5. **Clean up** - Remove the old version once the new version is stable

## Prerequisites

Before following these guides, ensure you have:

* A working llm-d deployment (see [getting started guide](../../docs/getting-started/README.md))
* Access to kubectl and the Kubernetes cluster
* Understanding of Kubernetes Gateway API concepts (for gateway mode)
* Familiarity with your model serving infrastructure (vLLM, etc.)
