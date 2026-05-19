# Rollout Guides

Rollout guides demonstrate how to perform incremental deployment operations that gradually introduce new versions of your inference infrastructure with minimal service disruption.

## Overview

These guides cover three critical rollout scenarios for LLM inference deployments:

1. **Rolling out a new model version** - Deploy new base model versions while maintaining service availability
2. **Rolling out a new LoRA adapter** - Update LoRA adapters without disrupting the underlying infrastructure
3. **Rolling out a new model server version** - Upgrade serving frameworks (e.g., vLLM versions) safely

## Available Guides

### [LoRA Adapter Rollout](./adapter-rollout.md)

Demonstrates how to perform gradual rollouts of LoRA adapter versions using the `InferenceModelRewrite` resource and traffic splitting. This guide shows how to:

* Establish a baseline with version aliases
* Perform gradual traffic splits (90/10, 50/50, 100/0)
* Test traffic distribution
* Complete the rollout and clean up old versions

**Use this guide when:** You need to deploy new versions of LoRA adapters without disrupting service.

### [InferencePool Rollout](./inferencepool-rollout.md)

Demonstrates how to perform infrastructure and model updates using InferencePool rollouts with HTTPRoute-based traffic splitting. This guide covers:

* Node (compute/accelerator) updates
* Base model version rollouts  
* Model server framework updates (e.g., vLLM version upgrades)
* Traffic splitting with HTTPRoute
* Rollback capabilities

**Use this guide when:** You need to update infrastructure, base models, or serving frameworks.

## General Rollout Strategy

All rollout guides follow a similar pattern:

1. **Deploy new infrastructure** - Create the new version alongside the existing one
2. **Configure traffic splitting** - Gradually shift traffic to the new version (e.g., 10% → 50% → 100%)
3. **Monitor and validate** - Verify the new version performs correctly at each stage
4. **Complete rollout** - Direct 100% of traffic to the new version
5. **Clean up** - Remove the old version once the new version is stable

## Prerequisites

Before following these guides, ensure you have:

* A working llm-d deployment (see [getting started guide](../prereq/README.md))
* Access to kubectl and the Kubernetes cluster
* Understanding of Kubernetes Gateway API concepts
* Familiarity with your model serving infrastructure (vLLM, etc.)