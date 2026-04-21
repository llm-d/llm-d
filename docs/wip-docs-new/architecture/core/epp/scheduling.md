# EPP Scheduler

The EPP Scheduler is a highly modular and extensible component within the Endpoint Picker (EPP) designed to select the optimal model server (endpoint) for an inference request. It leverages a plugin-based architecture, allowing for sophisticated scheduling strategies based on real-time metrics, prefix cache tracking, and model-specific requirements like LoRA adapters.

## Architecture Overview

At its core, the scheduler follows a **Filter -> Score -> Pick** lifecycle for every request. It orchestrates multiple **SchedulerProfiles**, each defining a specific set of plugins for filtering and scoring candidate endpoints.

```mermaid
flowchart TD
    Req[Inference Request] --> S[Scheduler.Schedule]
    
    subgraph Cycle [Scheduling Cycle]
        direction TD
        S --> Pick[ProfileHandler.Pick]
        Pick -->|Profiles| Loop{For each Profile}
        
        subgraph Exec [Profile Execution]
            direction TD
            Loop --> Filters[Filters]
            Filters --> Scorers[Scorers]
            Scorers --> Picker[Picker]
            Picker --> Result[ProfileResult]
        end
        
        Result -->|Collect| Pick
        Pick -->|Done| PRs[ProfileHandler.ProcessResults]
    end
    
    PRs --> Target["Selected Endpoint(s)"]

    %% Styling
    style Req fill:#e1f5fe,stroke:#01579b,color:#000
    style S fill:#e8f5e9,stroke:#2e7d32,color:#000
    style Pick fill:#fffde7,stroke:#fbc02d,color:#000
    style Loop fill:#fff,stroke:#333,color:#000
    style Filters fill:#fff3e0,stroke:#ff9800,color:#000
    style Scorers fill:#fff3e0,stroke:#ff9800,color:#000
    style Picker fill:#fff3e0,stroke:#ff9800,color:#000
    style Result fill:#f1f8e9,stroke:#558b2f,color:#000
    style PRs fill:#fce4ec,stroke:#c2185b,color:#000
    style Target fill:#f3e5f5,stroke:#7b1fa2,color:#000
```

### Core Components

*   **Scheduler**: The main orchestrator that manages the scheduling cycle. It invokes the configured `ProfileHandler` to pick profiles and then runs the selected profiles to obtain target endpoints.
*   **InferenceRequest**: A structured internal representation of the incoming request produced by the [`Parser`](request-handling.md), including the target model, parsed body (Completions, ChatCompletions, etc.), headers, and objectives.
*   **Endpoint**: Represents a candidate serving engine, with its metadata (e.g., Pod name, namespace and port) and state (e.g., active models, queue depth and KV-cache). Note that a Pod may run one or more endpoints each on a different port, this is case in [the data parallel deployment mode](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/).

### Extension Points

The scheduler's logic is distributed across several extension points, implemented via plugin interfaces:

1.  **ProfilePicker**: (Implemented by `ProfileHandler`) Selects which `SchedulerProfile`s to run based on the request and previous cycle results.
2.  **Filter**: Narrows down the list of candidate endpoints (e.g., based on health, SLO headroom, or cache affinity).
3.  **Scorer**: Assigns a score between `0.0` and `1.0` to each filtered endpoint. Multiple scorers can be weighted and combined.
4.  **Picker**: Selects the final endpoint(s) from the scored list (e.g., highest score, weighted random).
5.  **ProcessResults**: (Implemented by `ProfileHandler`) Aggregates the results from all executed profiles to produce the final `SchedulingResult`.

### Scheduler Profile

A `SchedulerProfile` is a configured pipeline consisting of:
*   **Filters**: A list of `Filter` plugins run sequentially.
*   **Scorers**: A list of `WeightedScorer` objects, where each contains a `Scorer` plugin and its relative weight.
*   **Picker**: A single `Picker` plugin that makes the final selection.

When a profile runs, it first filters the candidate endpoints. If any remain, it calculates a weighted aggregate score for each and then passes the scored list to the picker. The final score for an endpoint is calculated by multiplying the score returned by each scorer (which is bounded between 0.0 and 1.0) by its configured weight, and summing these weighted scores together. For example, if Scorer A (weight 2.0) returns 0.8 and Scorer B (weight 1.0) returns 0.5, the endpoint's final score is `(0.8 * 2.0) + (0.5 * 1.0) = 2.1`.

---

## Concrete Plugins

> [!IMPORTANT]
> Not all of the plugins listed below are configured by default. Only a curated subset is enabled in the [default configuration](placeholder-link).

### Filters
*   **[`prefix-cache-affinity-filter`](placeholder-link)**: A probabilistic filter that narrows candidates to "sticky" endpoints (those with high prefix cache scores). It includes a "TTFT load gate" to break stickiness if sticky endpoints are significantly slower than non-sticky ones.
*   **[`slo-headroom-tier-filter`](placeholder-link)**: Filters endpoints based on SLO headroom tiers to ensure quality of service.
*   **[`label-selector-filter`](placeholder-link)**: Keeps endpoints that matches a configured label selector.
*   **[`prefill-endpoints-filter`](placeholder-link)**: A special instance of `label-selector-filter` that retains only endpoints with a prefill label.
*   **[`decode-endpoints-filter`](placeholder-link)**: A special instance of `label-selector-filter` that retains only endpoints with a decode label.

### Scorers

*For details on exactly how each scorer calculates its score (0.0 to 1.0), please refer to the specific plugin's documentation.*

*   **[`kv-cache-utilization-scorer`](placeholder-link)**: Prefers endpoints with lower KV cache utilization to avoid fragmentation.
*   **[`latency-scorer`](placeholder-link)**: Scores endpoints based on predicted latency headroom, defined as the gap between the predicted request latency and the user's SLO if set.
*   **[`lora-affinity-scorer`](placeholder-link)**: Prefers endpoints that already have the requested LoRA adapter active or have capacity to load it.
*   **[`prefix-scorer`](placeholder-link)**: Scores based on the length of the prefix cache match.
*   **[`queue-depth-scorer`](placeholder-link)**: Prefers endpoints with shorter request queues.
*   **[`running-requests-scorer`](placeholder-link)**: Scores based on the number of currently active requests.
*   **[`token-load-scorer`](placeholder-link)**: Scores based on the total token load (input + output) handled by the endpoint.
*   **[`precise-prefix-cache-scorer`](placeholder-link)**: Scores requests based on real-time KV-cache locality. While the `prefix-scorer` relies on historical scheduling estimates, this version tracks actual cache states via model server events to ensure higher precision.
*   **[`session-affinity-scorer`](placeholder-link)**: Assigns a maximum score to the specific endpoint that handled previous requests for the same session, while all other endpoints receive the minimum score.
*   **[`no-hit-lru-scorer`](placeholder-link)**: For cold requests (zero cache hits), the scorer prioritizes endpoints that have never handled one, followed by those used least recently. This ensures an even distribution of the intensive "prefill" workload across the cluster. If a request has existing cache hits, the scorer assigns equal scores to all endpoints (scorer has no impact).


### Pickers
*   **[`max-score-picker`](placeholder-link)**: Selects the endpoint with the absolute highest score.
*   **[`random-picker`](placeholder-link)**: Selects an endpoint randomly from the candidates.
*   **[`weighted-random-picker`](placeholder-link)**: Selects an endpoint randomly, using the scores as relative probabilities (lottery scheduling).

### Profile Handlers
*   **[`single-profile-handler`](placeholder-link)**: Runs a single configured primary profile.
*   **[`disagg-profile-handler`](placeholder-link)**: Runs two scheduling profiles, one for prefill and one for decode. The **decode endpoint** is set as the primary destination for the proxy to forward the original request, while the **prefill endpoint** is injected into the request as a specialized header.

---

## Advanced Use Cases: Prefill/Decode Disaggregation

The scheduler natively supports advanced routing paradigms, such as **Prefill/Decode Disaggregation (P/D Disagg)**. This is a serving technique where the initial prompt processing (prefill) and the subsequent token generation (decode) are handled by separate, specialized model servers.

In a P/D Disagg setup, the `ProfileHandler` orchestrates two separate `SchedulerProfiles`:
1.  **Prefill Profile**: Evaluates and scores endpoints specialized for compute-heavy prompt processing. It may use filters and scorers focused on prefix cache affinity, queue depth, or token load.
2.  **Decode Profile**: Evaluates and scores endpoints specialized for memory-bandwidth-bound token generation.


```mermaid
flowchart TD
    Req[Inference Request] --> S[Scheduler]
    S --> PH[ProfileHandler]
    
    subgraph Profiles [Scheduling Profiles]
        direction TB
        P1[Prefill Profile]
        P2[Decode Profile]
    end

    PH -->|Pick| Profiles
    Profiles -->|Results| PR[Process Results]
    PR --> Target[Selected Endpoints]
```

The `ProfileHandler` uses the `Pick` extension point to determine which profiles need to run for a given request (e.g., if a request needs both prefill and decode, or just decode if the KV cache is already transferred). If both are needed, the prefill and decode endpoints are picked at the same time. The `ProfileHandler` then uses the `ProcessResults` extension point to merge the results from both profiles. This merging ensures that the **decode endpoint** is returned as the primary destination for the proxy to forward the original request. Simultaneously, the **prefill endpoint** is injected into the request as a specialized header. When the request reaches the decode worker, the **sidecar** running alongside the decoder intercepts it, extracts the prefill endpoint from the header, and coordinates a remote prefill from the selected prefill worker before the decoding process begins.

See [Disaggregated Serving](../../advanced/disaggregation.md) for more details on the design and request flow.
