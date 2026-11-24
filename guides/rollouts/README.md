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

A Blue-Green Update creates a second complete InferencePool and uses HTTPRoute to control traffic distribution between the old (blue) and new (green) versions. This strategy requires llm-d router gateway mode. It supports node/accelerator hardware upgrades, base model rollouts, and model server framework updates.

**How it works:**

- Deploy a complete new InferencePool alongside the existing one
- Optionally mirror live requests to the new pool first, if your gateway supports percentage mirroring (`HTTPRouteRequestPercentageMirror`) to an InferencePool
- Use HTTPRoute to gradually shift traffic (e.g., 1% → 5% → 10% → 50% → 100%)
- Instant rollback by adjusting HTTPRoute weights

**Use Blue-Green Updates for:**

- Critical, high-risk production deployments that require gradual canary rollouts
- Scenarios requiring fast rollbacks
- Header-based routing (e.g., routing beta users to new version)
- Updates that need precise traffic control

**Guide:** [Blue-Green Update](./blue-green-update.md)

### Disaggregated (P/D) Rollouts

Prefill/decode deployments on a `DisaggregatedSet` need every request's prefill and decode pods to run the same version, and prefill pods to stay up until decode pods have pulled their KV cache. The router's rollout screener and graceful drain settings cover both, with either an automatic rolling update or a step-by-step canary across two sets.

**Guide:** [Safe Rollouts with DisaggregatedSet](../../docs/operations/disaggregation/safe-rollouts.md)

### LoRA Adapter Rollout

LoRA (Low-Rank Adaptation) adapter rollouts allow you to update model customizations without changing the base model or infrastructure. This works in both standalone and llm-d router gateway modes.

**How it works:**

- Use `InferenceModelRewrite` to map stable model names to specific adapter versions
- Gradually shift traffic between adapter versions
- No infrastructure changes required

**Use LoRA Adapter Rollouts when:**

- You need to deploy new versions of LoRA adapters without disrupting service
- You want to test adapter changes with a subset of traffic
- You need to maintain multiple adapter versions simultaneously

**Guide:** [LoRA Adapter Rollout](./adapter-rollout.md)

## Strategy Comparison

| Feature | Rolling Update | Blue-Green Update |
| --------- | --------------- | ------------------- |
| **Routing Control** | Random/Even across all healthy pods | Precise Percentage (e.g., exactly 1% or 10%) |
| **Blast Radius** | High (All users exposed randomly) | Low (Isolated to specified target weight) |
| **Rollback Speed** | Slow (Requires creating new pods in reverse) | Instant (Flip HTTPRoute weight back to 0) |
| **Resource Costs** | Low (Only temporary surge of pods) | High (Requires running two full environments) |
| **Version Coexistence** | Simultaneously active inside one Service | Strictly separated across two distinct Services |
| **Deployment Mode** | Standalone and Gateway | Gateway only |

LoRA adapter rollouts have no infrastructure cost and work in either mode, but apply only when the change is limited to adapter weights — not base model or serving framework changes.

> [!NOTE]
> Capacity management may also play a role in choosing between these strategies.

## General Rollout Pattern

All rollout guides follow a similar pattern:

1. **Deploy new infrastructure** - Create the new version alongside the existing one
2. **Configure traffic splitting** - Gradually shift traffic to the new version (e.g., 10% → 50% → 100%)
3. **Monitor and validate** - Verify the new version performs correctly at each stage
4. **Complete rollout** - Direct 100% of traffic to the new version
5. **Clean up** - Remove the old version once the new version is stable

## Prerequisites

Before following these guides, ensure you have:

- A working llm-d deployment (see [getting started guide](../../docs/getting-started/README.mdx))
- Access to kubectl and the Kubernetes cluster
- Understanding of Kubernetes Gateway API concepts (for gateway mode)
- Familiarity with your model serving infrastructure (vLLM, etc.)

## (Optional) Enable Monitoring

Use the monitoring configuration of the deployment you are updating:

1. [Set up the monitoring stack](../../docs/operations/observability/setup.md), or use your existing Prometheus instance.
2. [Collect metrics](../../docs/operations/observability/metrics.md) from the router and model servers in both the incumbent and candidate deployments. Confirm both have healthy scrape targets before comparing them.
3. [Load Grafana dashboards](../../docs/operations/observability/dashboards.md) and select the relevant namespaces and pods.
4. [Configure alerts](../../docs/operations/observability/alerting.md) for the deployment's error and latency objectives.

## Observability & Troubleshooting

Record a baseline before changing traffic weights, then compare the candidate and incumbent at each step using comparable prompts, output lengths, and concurrency. Keep their series separate using the namespace, pod, or scrape labels present in your deployment; aggregating both versions can hide a candidate regression. Use `rate()` or `increase()` for counters because new pods start new time series.

### Key metrics for this path

| Signal | Why it matters during a rollout | Where to look |
| -------- | -------------------------------- | --------------- |
| `llm_d_epp_request_total`, `llm_d_epp_request_error_total` | Compare traffic volume and error rate before promoting a candidate. Also check client/gateway failures, including interrupted streams, because EPP metrics do not cover failures that bypass the EPP. | [Request metrics](../../docs/operations/observability/router-metrics.md#request-and-latency), [PromQL](../../docs/operations/observability/promql.md) |
| `llm_d_epp_ready_endpoints`, `vllm:num_requests_running`, `vllm:num_requests_waiting` | Confirm replacement capacity is Ready and watch for queue growth as old replicas drain. The ready-endpoint gauge has a `name` label for the InferencePool. | [Pool metrics](../../docs/operations/observability/router-metrics.md#inference-pool), [model server metrics](../../docs/operations/observability/model-server-metrics.md#vllm) |
| `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`, prefix-cache hits and queries | Distinguish a temporary cold-cache TTFT increase from sustained latency or queueing regressions. Compare histogram percentiles and cache-hit rates over the same observation window. | [Model server metrics](../../docs/operations/observability/model-server-metrics.md#vllm), [PromQL](../../docs/operations/observability/promql.md) |
| `llm_d_epp_model_rewrite_decisions_total` | For LoRA rollouts, inspect decisions by `model_rewrite_name`, `model_name`, and `target_model` to confirm the alias selects the intended versions. A rewrite decision alone does not prove the adapter loaded or inference succeeded. | [Router metrics](../../docs/operations/observability/router-metrics.md#scheduler-plugins--processing-overhead), [adapter verification](./adapter-rollout.md#step-2-gradual-rollout) |
| `llm_d_epp_disaggregatedset_revision_gating_share`, `llm_d_epp_disaggregatedset_strict_revision_no_match_total` | For P/D rollouts with the screener enabled, inspect each revision's assigned share and strict revision selections with no matching Ready endpoint. | [Disaggregation and rollout metrics](../../docs/operations/observability/router-metrics.md#disaggregation--rollout), [rollout verification](../../docs/operations/disaggregation/safe-rollouts.md#verify-a-rollout) |

For SGLang deployments, use the equivalent running-request, queue, latency, and cache signals in the [SGLang metric reference](../../docs/operations/observability/model-server-metrics.md#sglang).

### Validate each rollout step

- **Blue-green:** verify the `HTTPRoute` has `Accepted=True` and `ResolvedRefs=True` for the intended Gateway, then confirm traffic reaches each pool from its model server logs and metrics. Weights describe a traffic proportion; a small sample need not match it exactly. Mirrored responses are discarded, so successful client requests alone do not validate the green pool. Keep the old pool available for rollback until the candidate meets your error and latency objectives, then allow old requests to drain before deleting it.
- **LoRA adapters:** compare the response `model` values with the intended version names as in the [traffic test](./adapter-rollout.md#test-traffic-script), alongside rewrite decisions and inference errors. Check adapter availability and load failures on each serving pod; a response from a load-balanced `/v1/models` endpoint only describes the pod that answered. Keep the previous adapter available until its in-flight requests complete.
- **Disaggregated P/D:** inspect Ready pods by revision and role and the `x-llm-d-disagg-revision` response header. That header identifies the decode revision; correlate the routing proxy's selected prefill addresses with pod revision labels to check pairing. Collect logs before removing old pods and check NIXL transfer failures during drains. Follow [Verify a Rollout](../../docs/operations/disaggregation/safe-rollouts.md#verify-a-rollout) for the commands and limitations.

### Common failure modes

| Symptom | What to check first |
| --------- | --------------------- |
| Candidate pods remain `Pending` | Inspect `kubectl describe pod` events for accelerator, memory, or placement constraints. Blue-green and rolling-update surge replicas need spare capacity; see the [DisaggregatedSet capacity caveat](../../docs/operations/disaggregation/disaggregatedset.md#known-issue-rolling-updates-can-stall-on-fully-allocated-clusters). |
| Candidate pods run but remain `NotReady` | Check model-loading logs, startup/readiness probe paths and ports, and container restarts. See [Readiness Probe Troubleshooting](../../docs/operations/lifecycle/readiness-probes.md#troubleshooting). |
| The green pool receives no traffic | Inspect the HTTPRoute's status conditions, backend names and weights, the InferencePool selector, and Ready endpoints. Check the green pool's scrape targets before interpreting an empty dashboard as zero traffic. |
| Requests fail when old pods terminate | Check signal forwarding and budget `terminationGracePeriodSeconds` for `preStop`, vLLM's `--shutdown-timeout`, and teardown. See [Graceful Shutdown Troubleshooting](../../docs/operations/lifecycle/graceful-shutdown.md#troubleshooting). For P/D, also retain prefill KV long enough for decode to fetch it; see [Drain Pods Gracefully](../../docs/operations/disaggregation/safe-rollouts.md#step-2-drain-pods-gracefully). |
| An adapter alias stays on the old version or returns model-not-found errors | Check the rewrite's `poolRef`, match and target names, then the decision counter and the selected pods' adapter files and load logs. See [LoRA Adapter Rollout](./adapter-rollout.md). |
| P/D returns `503` with `screeners eliminated all endpoint candidates` | Ensure at least one revision has a Ready pod of every required role and that the screener and InferencePool selectors cover it. Follow [Safe Rollout Troubleshooting](../../docs/operations/disaggregation/safe-rollouts.md#troubleshooting). |

For shared queries and alert rules, see the [PromQL reference](../../docs/operations/observability/promql.md) and [Alerting](../../docs/operations/observability/alerting.md).
