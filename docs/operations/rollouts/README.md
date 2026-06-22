# Rollout Guides

Rollout guides demonstrate how to perform incremental deployment operations that gradually introduce new versions of your inference infrastructure with minimal service disruption.

## Overview

These guides cover rollout strategies for LLM inference deployments, helping you choose the right approach based on your requirements.

## Rollout Strategies

### Rolling Update

A Rolling Update is the standard Kubernetes deployment strategy that updates pods gradually within a single InferencePool. This approach works in both standalone and llm-d router gateway modes.

**How it works:**
- Updates pods incrementally (e.g., 25% at a time)
- Old pods continue serving traffic until new pods are healthy
- Built into Kubernetes Deployments

**Use Rolling Updates for:**
- General, non-critical updates where strict traffic percentages do not matter
- Scenarios where you want to conserve compute resources
- Development and staging environments

**Learn more:** [Kubernetes Rolling Update Tutorial](https://kubernetes.io/docs/tutorials/kubernetes-basics/update/update-intro/)

### Blue-Green Update (HTTPRoute Traffic Splitting)

A Blue-Green Update creates a second complete InferencePool and uses HTTPRoute to control traffic distribution between the old (blue) and new (green) versions. This strategy requires llm-d router gateway mode.

**How it works:**
- Deploy a complete new InferencePool alongside the existing one
- Use HTTPRoute to gradually shift traffic (e.g., 1% → 5% → 10% → 50% → 100%)
- Instant rollback by adjusting HTTPRoute weights

**Use Blue-Green Updates for:**
- Critical, high-risk production deployments that require gradual canary rollouts
- Scenarios requiring fast rollbacks
- Header-based routing (e.g., routing beta users to new version)
- Updates that need precise traffic control

**Guide:** [Blue-Green Update](./blue-green-update.md)

**Comparison:**

| Feature | Rolling Update | Blue-Green Update |
|---------|---------------|-------------------|
| **Routing Control** | Random/Even across all healthy pods | Precise Percentage (e.g., exactly 1% or 10%) |
| **Blast Radius** | High (All users exposed randomly) | Low (Isolated to specified target weight) |
| **Rollback Speed** | Slow (Requires creating new pods in reverse) | Instant (Flip HTTPRoute weight back to 0) |
| **Resource Costs** | Low (Only temporary surge of pods) | High (Requires running two full environments) |
| **Version Coexistence** | Simultaneously active inside one Service | Strictly separated across two distinct Services |
| **Deployment Mode** | Standalone and Gateway | Gateway only |

**Note:** Capacity management may also play a role in choosing between these strategies.

### LoRA Adapter Rollout

LoRA (Low-Rank Adaptation) adapter rollouts allow you to update model customizations without changing the base model or infrastructure. This works in both standalone and llm-d router gateway modes.

**How it works:**
- Use `InferenceModelRewrite` to map model names to specific adapter versions
- Gradually shift traffic between adapter versions
- No infrastructure changes required

**Use LoRA Adapter Rollouts when:**
- You need to deploy new versions of LoRA adapters without disrupting service
- You want to test adapter changes with a subset of traffic
- You need to maintain multiple adapter versions simultaneously

**Guide:** [LoRA Adapter Rollout](./adapter-rollout.md)

## General Rollout Pattern

All rollout guides follow a similar pattern:

1. **Deploy new infrastructure** - Create the new version alongside the existing one
2. **Configure traffic splitting** - Gradually shift traffic to the new version (e.g., 10% → 50% → 100%)
3. **Monitor and validate** - Verify the new version performs correctly at each stage
4. **Complete rollout** - Direct 100% of traffic to the new version
5. **Clean up** - Remove the old version once the new version is stable

## Prerequisites

Before following these guides, ensure you have:

* A working llm-d deployment (see [getting started guide](../../getting-started/README.md))
* Access to kubectl and the Kubernetes cluster
* Understanding of Kubernetes Gateway API concepts (for gateway mode)
* Familiarity with your model serving infrastructure (vLLM, etc.)