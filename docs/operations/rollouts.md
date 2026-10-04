# Rollouts

Rollouts are incremental deployment operations that gradually introduce new versions of inference infrastructure with minimal service disruption. Rather than replacing all running instances at once, a rollout shifts traffic progressively — allowing you to monitor behavior at each stage and roll back immediately if problems arise.

## Why Rollouts Matter for LLM Inference

LLM inference workloads have properties that make all-at-once replacements especially risky:

- **Long-running requests**: a request mid-generation cannot be migrated. Abrupt pod replacement causes visible failures.
- **Warm-up cost**: new model server pods require time to load model weights, compile CUDA graphs, and fill KV caches. Traffic arriving before warm-up completes sees high latency.
- **Resource intensity**: GPU memory is non-trivially expensive. Rolling back a bad deployment means waiting for a full cold-start cycle.

Gradual rollouts mitigate all three by keeping the stable version fully operational while the new version is validated under real traffic.

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

**Guide:** [Blue-Green Update](../../../guides/rollouts/blue-green-update.md)

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

**Guide:** [LoRA Adapter Rollout](../../../guides/rollouts/adapter-rollout.md)

## Strategy Comparison

| Feature | Rolling Update | Blue-Green Update |
|---------|---------------|-------------------|
| **Routing Control** | Random/Even across all healthy pods | Precise Percentage (e.g., exactly 1% or 10%) |
| **Blast Radius** | High (All users exposed randomly) | Low (Isolated to specified target weight) |
| **Rollback Speed** | Slow (Requires creating new pods in reverse) | Instant (Flip HTTPRoute weight back to 0) |
| **Resource Costs** | Low (Only temporary surge of pods) | High (Requires running two full environments) |
| **Version Coexistence** | Simultaneously active inside one Service | Strictly separated across two distinct Services |
| **Deployment Mode** | Standalone and Gateway | Gateway only |

LoRA adapter rollouts have no infrastructure cost and work in either mode, but apply only when the change is limited to adapter weights — not base model or serving framework changes.

> [!NOTE]
> Capacity management may also play a role in choosing between these strategies.
