# OpenCost Inference Costs: New Dimensions Plan

**Authors**: Sima Nadler (_IBM_)

Add `tenant_id`, `user_id`, and `workload_id` tracking to the OpenCost inference cost capabilities based on attribution metrics emitted by the llm-d stack.

See the following related documents:

- **Proposal for adding new dimensions to llm-d:** `request-attribution-metrics.md` — covers the changes required to emit attribution labels on Prometheus metrics and structured logs.
---

## 1. Proposed Implementation

### 1.1 New types (`core/pkg/source/inference_results.go`)

Add three new types to support per-attribution-dimension token sums:

```go
// InferenceDimensionKey identifies a (user_id, tenant_id, workload_id, serving_model, namespace)
// combination. UserID may be empty when the user_id label is absent from the attribution metrics.
type InferenceDimensionKey struct {
    UserID       string
    TenantID     string
    WorkloadID   string
    ServingModel string
    Namespace    string
}

// InferenceDimensionTokens holds the prompt and completion token counts for one
// dimension key, sourced from llm_d_epp_request_input_tokens_sum and
// llm_d_epp_request_output_tokens_sum respectively.
type InferenceDimensionTokens struct {
    PromptTokens     float64
    CompletionTokens float64
}

// InferenceDimensionResult holds per-dimension token counts from attribution metrics.
type InferenceDimensionResult struct {
    Values map[InferenceDimensionKey]*InferenceDimensionTokens
}
```

### 1.2 `MetricsQuerier` interface extension (`core/pkg/source/datasource.go`)

One new query method and its constant:

```go
QueryInferenceDimensionTokens = "QueryInferenceDimensionTokens"

// QueryInferenceDimensionTokens returns prompt and completion token sums from
// llm_d_epp_request_input_tokens_sum and llm_d_epp_request_output_tokens_sum,
// broken down by user_id (optional), tenant_id, workload_id, serving_model, and namespace.
QueryInferenceDimensionTokens(start, end time.Time) *Future[InferenceDimensionResult]
```

Stubs required in: `core/pkg/source/noop.go`, `record.go`, `mock.go`, and `modules/collector-source/pkg/collector/metricsquerier.go`.
Decoder required in: `core/pkg/source/decoders.go` (`DecodeInferenceDimensionResult`).

### 1.3 Prometheus queries (`modules/prometheus-source/pkg/prom/inference_queries.go`)

#### 1.3.1 `QueryInferenceDimensionTokens` — histogram `_sum` delta strategy

`llm_d_epp_request_input_tokens` and `llm_d_epp_request_output_tokens` are
**histograms**. Their `_sum` series accumulates the total tokens observed across
all requests and behaves as a monotonically increasing counter. OpenCost queries
the `_sum` series using the same `last_over_time` delta pattern as
`queryCounterDelta` — no separate `_total` counter metrics are needed.

`user_id` may or may not be present as a label on these series. A single PromQL
query covers both cases: when `user_id` is absent from the series, the
`sum by (…)` simply omits it and the resulting `InferenceDimensionKey` is built
with `UserID = ""`.

`QueryInferenceDimensionTokens` issues **two queries per token type** (four
total: input + output, each at start + end of window):

- **End-of-window query** (input tokens, `user_id` present):
  ```promql
  sum by (user_id, tenant_id, workload_id, serving_model, namespace) (
    last_over_time(llm_d_epp_request_input_tokens_sum[<window>m] @ <end_unix>)
  )
  ```
- **Start-of-window query** (narrow lookback to anchor the delta):
  ```promql
  sum by (user_id, tenant_id, workload_id, serving_model, namespace) (
    last_over_time(llm_d_epp_request_input_tokens_sum[2m] @ <start_unix>)
  )
  ```
  Delta = end value − start value per key. Negative delta (counter reset) → use end value.

Where `<window>m` = `windowDuration.Minutes()` (minimum 2), matching the
`queryCounterDelta` convention. Both queries are issued at `effectiveEnd`
(clamped to `time.Now()`) to avoid future-timestamp errors.

The same pattern applies to `llm_d_epp_request_output_tokens_sum`. The helper
`queryDimensionCounterDelta` encapsulates the start/end pair and is called twice
(once per metric). Results are merged into a single `InferenceDimensionResult`,
with `UserID = ""` for entries where `user_id` is absent from the series labels.

If `INFERENCE_ATTRIBUTION_ENABLED` is false,
`QueryInferenceDimensionTokens` returns an empty result immediately without
querying Prometheus.

### 1.4 `InferenceCost` type extensions

Add three fields to each struct, and update `newInferenceCostResponse()` to copy them:

```go
// pkg/inferencecost/types.go — InferenceCostProperties
// Empty when not broken down by dimension, or when user_id is absent from attribution metrics (UserID only).
UserID     string
TenantID   string
WorkloadID string

// pkg/inferencecost/apitypes.go — InferenceCostAPIProperties
UserID     string `json:"user_id,omitempty"`
TenantID   string `json:"tenant_id,omitempty"`
WorkloadID string `json:"workload_id,omitempty"`
```

### 1.5 Collector changes (`pkg/inferencecost/collector.go`)

**Call ordering** — the new `buildDimensionCosts()` depends on per-million rates computed by the calculator. Because the calculator currently runs in `runner.go` (not inside `CollectMetrics`), `buildDimensionCosts()` must also be called from `runner.go` after `calculator.CalculateCosts()`. The sequence becomes:

```
// in runner.go runOnce():
1. modelCosts, err := collector.CollectMetrics()  // returns model-level slice; also stores InferenceDimensionResult internally
2. calculator.CalculateCosts(modelCosts)           // populates InputCostPerMillionTokens, OutputCostPerMillionTokens
3. dimCosts := collector.BuildDimensionCosts(modelCosts) // reads stored InferenceDimensionResult; produces dimension-level []*InferenceCost
4. exporter.Export(modelCosts, dimCosts)           // exports both slices
```

The alternative of moving step 3 inside `CollectMetrics` would require the calculator to run inside the collector, creating a circular dependency. Keeping `BuildDimensionCosts` as a separate method on `Collector` and calling it from `runner.go` preserves the existing separation.

**`buildDimensionCosts()` join formula**:

```go
// For each (user_id, tenant_id, workload_id, serving_model, namespace) in dimensionTokens:
//   look up model cost by serving_model:namespace
//   apply join formula independently per cost basis:

AllocationTotalCost = promptTokens × (InputCostPerMillionTokens[allocation] / 1_000_000)
                    + completionTokens × (OutputCostPerMillionTokens[allocation] / 1_000_000)

UsageTotalCost      = promptTokens × (InputCostPerMillionTokens[usage] / 1_000_000)
                    + completionTokens × (OutputCostPerMillionTokens[usage] / 1_000_000)
```

Token counts are set directly from `InferenceDimensionTokens` (not scaled fractions). All other `Properties` fields (namespace, cluster, model name, etc.) are copied from the matched model entry. `AllocationMethod` is inherited from the model entry.

**`CollectMetrics` return type** stays `([]*InferenceCost, error)` — it returns only the model-level slice as today. The dimension token data (`InferenceDimensionResult`) is stored as a field on `Collector` after the collect step, allowing `BuildDimensionCosts` (called from `runner.go` after the calculator) to read it without changing `CollectMetrics`' signature. `queryservice.go`'s `computeStep` calls `BuildDimensionCosts` directly after calling `CollectMetrics` and the (local) calculator.

**Data quality check** — after building dimension costs, compare total attributed input token sums against vLLM prompt token totals per `serving_model:namespace`. Log a warning when the ratio falls below 0.9 (configurable), indicating requests are not being attributed:

```go
// sum(llm_d_epp_request_input_tokens_sum[model:ns]) / vllm_prompt_tokens[model:ns] < 0.9
// → log.Warnf("InferenceCost: attribution coverage low for model=%s ns=%s (%.0f%% of vLLM tokens attributed)")
```

### 1.6 Exporter (`pkg/inferencecost/exporter.go`)

Add `dimensionCost` gauge and update `Export()` to accept and iterate the new dimension-level slice:

```go
dimensionCost = prometheus.NewGaugeVec(
    prometheus.GaugeOpts{
        Name: "llm_dimension_hourly_cost",
        Help: "...",
    },
    []string{"model_name", "namespace", "cost_basis", "tenant_id", "workload_id", "user_id"},
)
```

When `user_id` is empty (absent from the attribution metrics), the gauge is still emitted with an empty string label value — this is valid Prometheus behaviour and preserves `tenant_id`/`workload_id` attribution.

### 1.7 Aggregation & API

**`pkg/inferencecost/aggregate.go`** — add three new dimensions to `supportedAggregateProperties`, plus three new `case` arms in `aggKey()` and `matchesFilter()`, and three new property-clear guards in `aggregate()`.

**`pkg/inferencecost/queryservice.go` — `computeStep()`** — call `collector.BuildDimensionCosts(modelCosts, dimResult)` immediately after the local `CalculateCosts` call, then include dimension-level entries in the `InferenceCostSet`. Their properties carry all three new fields plus the full model/namespace/cluster properties, so existing aggregation by `model_name` or `namespace` correctly sums across users/tenants/workloads.

**`pkg/inferencecost/queryservice_helper.go`** — update validation error messages to list the three new supported dimensions: `user_id`, `tenant_id`, `workload_id`.

### 1.8 File change summary

| File | Change | What |
|---|---|---|
| `core/pkg/source/inference_results.go` | Add | `InferenceDimensionKey`, `InferenceDimensionTokens`, `InferenceDimensionResult` |
| `core/pkg/source/datasource.go` | Extend | `QueryInferenceDimensionTokens` constant + interface method |
| `core/pkg/source/decoders.go` | Add | `DecodeInferenceDimensionResult` |
| `core/pkg/source/noop.go` | Add | No-op impl for `QueryInferenceDimensionTokens` |
| `core/pkg/source/record.go` | Add | Recording impl for `QueryInferenceDimensionTokens` |
| `core/pkg/source/mock.go` | Add | Mock impl with override injection for `QueryInferenceDimensionTokens` |
| `modules/prometheus-source/pkg/prom/inference_queries.go` | Add | `QueryInferenceDimensionTokens` (histogram `_sum` delta strategy), `queryDimensionCounterDelta`, decoder, `mergeDimensionDeltas` |
| `modules/collector-source/pkg/collector/metricsquerier.go` | Add | Stub impl (returns empty) for `QueryInferenceDimensionTokens` |
| `pkg/inferencecost/types.go` | Extend | `UserID/TenantID/WorkloadID` on `InferenceCostProperties`; `AttributionEnabled` on `Config` |
| `pkg/inferencecost/env.go` | Extend | Reader for `INFERENCE_ATTRIBUTION_ENABLED` |
| `pkg/inferencecost/apitypes.go` | Extend | `UserID/TenantID/WorkloadID` on `InferenceCostAPIProperties`; update `newInferenceCostResponse` |
| `pkg/inferencecost/collector.go` | Extend | `QueryInferenceDimensionTokens` future; stores `InferenceDimensionResult` as field after collect; `BuildDimensionCosts(modelCosts)` public method; coverage warning |
| `pkg/inferencecost/aggregate.go` | Extend | 3 new dimensions in map + switch statements |
| `pkg/inferencecost/exporter.go` | Extend | `llm_dimension_hourly_cost` gauge; updated `Export` signature |
| `pkg/inferencecost/runner.go` | Extend | Call `collector.BuildDimensionCosts()` after `calculator.CalculateCosts()`; pass both slices to `exporter.Export()` |
| `pkg/inferencecost/queryservice.go` | Extend | Call `collector.BuildDimensionCosts()` after local `CalculateCosts()`; add dimension entries to `InferenceCostSet` |
| `pkg/inferencecost/queryservice_helper.go` | Extend | Updated error messages listing `user_id`, `tenant_id`, `workload_id` |

### 1.9 Tests

| Test file | What to add |
|---|---|
| `core/pkg/source/decoders_test.go` | `TestDecodeInferenceDimensionResult` — including the case where `user_id` label is absent from the series |
| `pkg/inferencecost/collector_test.go` | `buildDimensionCosts` unit tests: basic join formula, zero model cost (no panic), empty attribution result, missing `user_id` label, coverage warning when attributed token sums diverge from vLLM |
| `pkg/inferencecost/aggregate_test.go` | Aggregation by `user_id`/`tenant_id`/`workload_id`; filter by `tenant_id:"acme-corp"`; aggregation with empty `user_id` |
| `pkg/inferencecost/exporter_test.go` | `llm_dimension_hourly_cost` emitted correctly; empty `user_id` emitted as empty Prometheus label value |
| `modules/prometheus-source/pkg/prom/inference_queries_test.go` | `queryDimensionCounterDelta` unit tests: both token types present; one absent (graceful degradation); `user_id` label absent from result |

### 2 Key design decisions

| Decision | Chosen approach | Rationale |
|---|---|---|
| **Source metrics** | `llm_d_epp_request_input_tokens_sum` + `llm_d_epp_request_output_tokens_sum` | The histogram `_sum` series accumulate total tokens and behave as monotonically increasing counters — the `queryCounterDelta` pattern applies directly. No separate `_total` counters needed. |
| **Cost join formula** | `promptTokens × (inputCostPerM / 1M) + completionTokens × (outputCostPerM / 1M)` | Token sums are the cost drivers. Request count is not used — per-token rates differ between input and output. |
| **Calculator call order** | Calculator runs on model entries first; `buildDimensionCosts` uses the resulting rates | No need to re-run cost split logic per dimension entry. |
| **`user_id` cardinality** | `user_id` label may or may not be present on attribution metrics; empty string when absent | OpenCost does not impose its own cardinality cap — that decision belongs to the llm-d configuration. |
| **`CollectMetrics` signature** | Unchanged `([]*InferenceCost, error)`; `InferenceDimensionResult` stored as `Collector` field; `BuildDimensionCosts` is a separate public method | Calculator must run between collect and dimension-cost build; keeping `CollectMetrics` signature stable avoids breaking `runner.go`, `queryservice.go`, and test code that mocks the interface. |
| **`serving_model` join key** | Label on attribution metrics, populated from the vLLM response body; joined on `serving_model:namespace` in OpenCost | The value vLLM returns in the response body is guaranteed to match `vllm:prompt_tokens_total{model_name}` — join keys are consistent by construction. Falls back to `target_model_name` on error paths. |
| **`requested_model` excluded from group-by** | Omitted from `InferenceDimensionKey` and PromQL `sum by (…)` | Cost rate is driven by `serving_model`; including `requested_model` inflates cardinality without adding cost signal. Attribution by what was *served*, not what was *requested*, is the correct billing model. |
| **Backward compatibility** | New fields are empty-string by default; feature is opt-in via `INFERENCE_ATTRIBUTION_ENABLED=true` | Deployments without attribution configured are completely unaffected. |
---

### Appendix A — Multi-llm-d Deployment Issues

A cluster may contain multiple independent llm-d deployments, each in its own namespace with its own router, vLLM pods, and InferencePool. OpenCost is deployed once per cluster. Multi-deployment concerns are handled naturally: the attribution metrics carry a `namespace` label, and the `serving_model:namespace` composite key partitions dimension token data per deployment automatically — no manual per-namespace configuration is required.

One limitation pre-dates this plan and remains open:

#### Known limitation — Shared infra label is a single global value ⚠️

**The problem:** `INFERENCE_SHARED_INFRA_LABEL` and `INFERENCE_SHARED_INFRA_LABEL_VALUE` identify shared infrastructure pods whose costs are distributed across model pods. If two llm-d deployments use different label schemes for their shared infrastructure, there is no way to express different values per namespace.

**Severity:** Low in practice — llm-d standardises the `llm-d.ai/inference-shared=true` label across all deployments, so divergence is unlikely.

**Optional future resolution:** A per-namespace configuration mechanism (e.g. an annotation on the InferencePool or a dedicated ConfigMap) could allow OpenCost to discover the shared infra label per namespace. However, consuming this would require restructuring the allocation query logic to filter and aggregate per namespace rather than in a single cluster-wide pass — a more invasive change deferred to a future iteration.

#### What works correctly without changes

- **Model-level cost collection** — vLLM and allocation queries key by `(model_name, namespace)`, naturally partitioning deployments.
- **`buildDimensionCosts()` join** — keys on `serving_model:namespace`, costs attributed to the correct deployment.
- **Coverage warning** — compares attributed token sums vs vLLM token totals per `serving_model:namespace`, each deployment checked independently.
- **API filtering** — `?filter=namespace:"llm-d-prod"` isolates one deployment's costs from another.
