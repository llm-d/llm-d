THIS NEEDS TO BE UPDATED - Written by Claude

# Latency Predictor

The Latency Predictor is an experimental component that enables predicted-latency-based scheduling in llm-d.

## Functionality

The Latency Predictor is an ML-based system that continuously learns from live traffic to predict per-endpoint request latency. It enables the EPP to make SLO-aware routing decisions -- choosing model server pods based on whether they can meet a request's latency targets rather than relying solely on utilization metrics like queue depth or KV-cache utilization.

### Why Predicted Latency?

Utilization-based load balancing has fundamental limitations for LLM workloads:

- **Request costs vary enormously.** A 10-token prompt and a 10,000-token prompt impose very different loads, but both count as one request in a queue.
- **Cache reuse is unstable.** Prefix cache hit rates shift as traffic patterns change, making static weights unreliable.
- **Conflicting objectives.** Optimizing for cache reuse (consolidating traffic onto fewer pods) conflicts with optimizing for low time-per-output-token (spreading load across pods). No fixed weight configuration handles all conditions.
- **No SLO awareness.** Utilization-based scorers cannot answer the question "can this pod serve this request within the caller's latency budget?"

The Latency Predictor addresses these gaps by predicting **p90 TTFT** (time to first token) and **p90 TPOT** (time per output token) for each candidate pod, given the pod's current state and the request's characteristics. The EPP's `slo-scorer` plugin then compares these predictions against per-request SLO targets to route traffic intelligently.

## Design

### Architecture

The Latency Predictor runs as a set of sidecar containers alongside the EPP. Three container types work together:

```
                    ┌──────────────────────────────────────────────────────┐
                    │                    EPP Pod                          │
                    │                                                      │
                    │  ┌────────────┐       ┌───────────────────────────┐  │
                    │  │    EPP     │       │   Training Server         │  │
 Inference ────────►│  │            │       │   (port 8000)             │  │
 Requests           │  │  slo-scorer├──────►│                           │  │
                    │  │            │       │   Collects completed      │  │
                    │  │            │       │   request data, retrains  │  │
                    │  │            │       │   XGBoost models          │  │
                    │  │            │       └───────────┬───────────────┘  │
                    │  │            │                   │                  │
                    │  │            │          writes models (.joblib)     │
                    │  │            │                   │                  │
                    │  │            │                   ▼                  │
                    │  │            │       ┌──────────────────────────┐   │
                    │  │            │       │   Shared Volume          │   │
                    │  │            │       │   /models ──► /server_   │   │
                    │  │            │       │               models     │   │
                    │  │            │       └──────────────────────────┘   │
                    │  │            │                   ▲                  │
                    │  │            │          reads models (.joblib)      │
                    │  │            │                   │                  │
                    │  │            │       ┌───────────┴───────────────┐  │
                    │  │            ├──────►│   Prediction Servers      │  │
                    │  │            │predict│   (ports 8001, 8002, 8003)│  │
                    │  │            │◄──────┤                           │  │
                    │  │            │       │   Serve trained models,   │  │
                    │  │            │       │   return p90 TTFT/TPOT    │  │
                    │  └────────────┘       └───────────────────────────┘  │
                    └──────────────────────────────────────────────────────┘
```

#### Training Server

The training server (single instance, port 8000) continuously learns from completed requests:

1. The EPP sends completed request data (actual TTFT, TPOT, and associated features) to the training server.
2. The training server maintains a **stratified sliding-window dataset** -- samples are bucketed by KV-cache utilization (10% steps), prefix hit rate (0.25 steps), etc. to prevent the model from forgetting underrepresented traffic regimes.
3. The model is retrained at a configurable interval (default: every 1 second, once at least 100 samples are collected).
4. Updated model files (`.joblib`) are written to a shared volume.

#### Prediction Servers

Three prediction server instances (ports 8001, 8002, 8003) serve the trained models:

1. They read model files from the shared volume.
2. When the EPP needs to score candidate pods, a Go sidecar coalesces concurrent prediction requests within a 1ms window into a single batched HTTP call.
3. Requests are load-balanced across the three prediction server instances, each running 28 uvicorn workers.
4. Each prediction evaluates all candidate pods and returns predicted p90 TTFT and p90 TPOT per pod.

#### ML Model

The predictor uses **XGBoost quantile regression** (`reg:quantileerror`), chosen for its speed, accuracy, and online learning capability. Two independent models are trained -- one for TTFT and one for TPOT. Across benchmark runs, the models achieve approximately **5% Mean Absolute Percentage Error (MAPE)**.

The target quantile is configurable via `LATENCY_QUANTILE_ALPHA` (default: 0.9 for p90).

**TTFT model features:**

| Feature | What It Captures |
|---------|-----------------|
| KV Cache Usage % | How full the decode state is -- high utilization means higher TPOT and slower TTFT |
| Input Length | Weight of the prefill step -- longer prompts increase TTFT |
| Queue Depth | Backlog before scheduling -- more waiting requests increase TTFT |
| Running Requests | Active GPU concurrency -- higher concurrency increases both TTFT and TPOT |
| Prefix Cache Match % | KV reuse potential -- high match rates reduce TTFT |
| Input Tokens In Flight | Tokens dispatched but not yet prefilled, plus tokens still in KV cache -- captures incoming prefill pressure |

**TPOT model features:**

| Feature | What It Captures |
|---------|-----------------|
| KV Cache Usage % | KV-cache utilization |
| Input Length | Input token count |
| Queue Depth | Queued requests |
| Running Requests | Active requests / GPU concurrency |
| Tokens Generated | Output tokens produced so far |

### How Endpoint Selection Works

#### With SLO Headers

1. The EPP calls the prediction servers to get predicted p90 TTFT and TPOT for each candidate pod.
2. For each pod, the scorer computes **headroom**: `SLO target - predicted latency`.
3. Pods are classified into **positive headroom** (can meet SLOs) and **negative headroom** (cannot meet SLOs) buckets.
4. Within the positive bucket, the scorer selects the pod with the **least positive headroom** (best-fit packing), keeping other pods free for future requests.
5. Headroom is computed as a weighted blend, defaulting to 80% TTFT + 20% TPOT.

#### Without SLO Headers

When `x-prediction-based-scheduling: true` is set but no SLO targets are provided (treated as SLO=0), the system routes to the pod with the **lowest predicted latency**.

#### Request Shedding

If a request has priority < 0 and no pod can meet both TTFT and TPOT SLOs, the request is **shed** rather than routed to a pod that will miss the SLO.

#### Cache-Aware Affinity

The scorer integrates prefix cache awareness with an epsilon-greedy strategy:

- **Exploit (99%)**: Filter candidates to pods whose prefix cache score exceeds the affinity threshold. Among those, select the pod with the best predicted latency.
- **Explore (1%)**: Ignore the affinity gate and consider all pods, seeding cache entries on non-sticky pods for diversity.
- **Load gate**: If the best sticky pod's predicted TTFT exceeds the best overall pod's TTFT by more than a configurable penalty threshold, affinity is broken in favor of latency.

### Current Limitations

| Limitation | Details |
|------------|---------|
| Homogeneous pools only | All pods must have the same GPU type, model weights, and serving configuration. |
| Streaming only | Only streaming workloads (`"stream": "true"`) are supported for training data collection. |
| p90 only | Only p90 TTFT and TPOT are predicted. Other percentiles (p95, p99) are not yet available. |
| No prefill/decode disaggregation | Prediction assumes a pod executes the entire request lifecycle. |
| Unvalidated with advanced features | Not tested with LoRA adapters, speculative decoding, or beam search. |

### Scaling

Each prediction server can sustain approximately **300 QPS** (tested on c4-standard-192 with ~192 vCPUs). Scale horizontally by adding more prediction server sidecars and updating `PREDICTION_SERVER_URL` accordingly.

| QPS | Avg Latency (ms) | p99 Latency (ms) | Prediction Servers |
|-----|-------------------|-------------------|--------------------|
| 100 | 3.5 | 46 | 1 |
| 1,000 | 5.0 | 49 | 2 |
| 2,500 | ~19 | ~36 | 1 |
| 5,000 | ~27 | ~74 | 1 |
| 7,500 | ~35 | ~96 | 3 |
| 10,000 | ~48 | ~137 | 4 |

## Configuration

### Enabling the Latency Predictor

Add the `--enable-latency-predictor` flag to the EPP container args:

```yaml
args:
  - --config-file=/config/default-plugins.yaml
  - --enable-latency-predictor
```

### EPP Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PREDICTION_SERVER_URL` | CSV of in-pod predictor endpoints | `http://localhost:8001,http://localhost:8002,http://localhost:8003` |
| `TRAINING_SERVER_URL` | Training server endpoint | `http://localhost:8000` |
| `LATENCY_MAX_SAMPLE_SIZE` | Max sample size for latency data | |
| `HEADROOM_TTFT_WEIGHT` | Weight for TTFT in positive headroom scoring | `0.8` |
| `HEADROOM_TPOT_WEIGHT` | Weight for TPOT in positive headroom scoring | `0.2` |
| `NEG_HEADROOM_TTFT_WEIGHT` | Weight for TTFT in negative headroom scoring | |
| `NEG_HEADROOM_TPOT_WEIGHT` | Weight for TPOT in negative headroom scoring | |
| `HEADROOM_SELECTION_STRATEGY` | `least` (compact/best-fit) or `most` (spread) | `least` |
| `SLO_BUFFER_FACTOR` | Safety multiplier on TPOT SLOs | |

### Training Server ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: latency-predictor-config
data:
  LATENCY_RETRAINING_INTERVAL_SEC: "1"
  LATENCY_MIN_SAMPLES_FOR_RETRAIN: "100"
  LATENCY_TTFT_MODEL_PATH: "/models/ttft.joblib"
  LATENCY_TPOT_MODEL_PATH: "/models/tpot.joblib"
  LATENCY_TTFT_SCALER_PATH: "/models/ttft_scaler.joblib"
  LATENCY_TPOT_SCALER_PATH: "/models/tpot_scaler.joblib"
  LATENCY_MODEL_TYPE: "xgboost"
  LATENCY_MAX_TRAINING_DATA_SIZE_PER_BUCKET: "5000"
```

| Variable | Description |
|----------|-------------|
| `LATENCY_RETRAINING_INTERVAL_SEC` | How often (in seconds) the model is retrained from collected samples. |
| `LATENCY_MIN_SAMPLES_FOR_RETRAIN` | Minimum number of completed request samples required before retraining. |
| `LATENCY_MODEL_TYPE` | ML algorithm used for prediction. Currently only `xgboost` is supported. |
| `LATENCY_MAX_TRAINING_DATA_SIZE_PER_BUCKET` | Maximum samples retained per stratification bucket in the sliding window. |
| `LATENCY_QUANTILE_ALPHA` | Target quantile for prediction (default: `0.9` for p90). |
| `LATENCY_TEST_TRAIN_RATIO` | Fraction of data held out for evaluation (default: `0.1`). |
| `LATENCY_MAX_TEST_DATA_SIZE` | Maximum number of test samples (default: `1000`). |
| `LATENCY_SAMPLE_WEIGHTING_FOR_PREFIX_CACHE` | When `true`, reweights underrepresented prefix cache buckets during training (default: `false`). |

### Prediction Server ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prediction-server-config
data:
  LATENCY_MODEL_TYPE: "xgboost"
  PREDICT_HOST: "0.0.0.0"
  LOCAL_TTFT_MODEL_PATH: "/server_models/ttft.joblib"
  LOCAL_TPOT_MODEL_PATH: "/server_models/tpot.joblib"
  LOCAL_TTFT_SCALER_PATH: "/server_models/ttft_scaler.joblib"
  LOCAL_TPOT_SCALER_PATH: "/server_models/tpot_scaler.joblib"
```

### Plugin Configuration

The `plugins-config` ConfigMap defines two scheduling profiles -- `default` for baseline routing and `slo` for prediction-based routing:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
  - type: queue-scorer
  - type: kv-cache-utilization-scorer
  - type: prefix-cache-scorer
  - type: slo-request-tracker
  - type: slo-scorer
  - type: slo-aware-profile-handler
  - type: max-score-picker

schedulingProfiles:
  - name: default
    plugins:
      - pluginRef: slo-request-tracker
      - pluginRef: prefix-cache-scorer
      - pluginRef: queue-scorer
      - pluginRef: kv-cache-utilization-scorer
      - pluginRef: max-score-picker

  - name: slo
    plugins:
      - pluginRef: prefix-cache-scorer
        weight: 0
      - pluginRef: slo-request-tracker
      - pluginRef: slo-scorer
      - pluginRef: max-score-picker
```

**Key plugins:**

| Plugin | Role |
|--------|------|
| `slo-request-tracker` | Captures per-request SLO targets from headers and tracks them through the request lifecycle. |
| `slo-scorer` | Calls the prediction servers, compares predicted TTFT/TPOT against SLO targets, and scores pods by headroom. |
| `slo-aware-profile-handler` | Automatically switches requests into the `slo` profile when SLO headers are present. |

### Headroom Strategies

The `HEADROOM_SELECTION_STRATEGY` environment variable controls how pods are selected from the positive headroom bucket:

- **`least`** (default) -- Best-fit / compact. Routes to the pod with the least headroom above the SLO, packing requests to keep other pods free for burst capacity.
- **`most`** -- Spread. Routes to the pod with the most headroom, distributing load evenly across the pool.

### Per-Request SLO Headers

Clients enable SLO-aware routing by setting HTTP headers on inference requests:

| Header | Description |
|--------|-------------|
| `x-prediction-based-scheduling: true` | Activates SLO-aware routing for the request. |
| `x-slo-ttft-ms: <value>` | Target time-to-first-token in milliseconds. |
| `x-slo-tpot-ms: <value>` | Target time-per-output-token in milliseconds. |

## Examples

### Sending a Request with SLO Headers

```bash
curl $GATEWAY_IP/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'x-prediction-based-scheduling: true' \
  -H 'x-slo-ttft-ms: 200' \
  -H 'x-slo-tpot-ms: 50' \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "prompt": "Explain the difference between prefill and decode.",
    "max_tokens": 200,
    "temperature": 0,
    "stream": "true",
    "stream_options": {"include_usage": "true"}
  }'
```

### Response Observability

When the latency predictor is enabled, the final SSE frame in streaming responses includes both predicted and actual latency metrics:

```json
{
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 200,
    "ttft_ms": 59,
    "avg_tpot_ms": 7.5,
    "predicted_ttft_ms": 273.23,
    "avg_predicted_tpot_ms": 97.19,
    "tpot_observations_ms": [9, 6],
    "predicted_tpot_observations_ms": [176.22, 18.17]
  }
}
```

This allows users to compare predictions against actuals and validate model accuracy. TPOT is sampled every 200th output token.

### Full Plugin Configuration

A complete `EndpointPickerConfig` with both default and SLO-aware profiles:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
  - type: queue-scorer
  - type: kv-cache-utilization-scorer
  - type: prefix-cache-scorer
  - type: slo-request-tracker
  - type: slo-scorer
  - type: slo-aware-profile-handler
  - type: max-score-picker

schedulingProfiles:
  - name: default
    plugins:
      - pluginRef: slo-request-tracker
      - pluginRef: prefix-cache-scorer
      - pluginRef: queue-scorer
      - pluginRef: kv-cache-utilization-scorer
      - pluginRef: max-score-picker

  - name: slo
    plugins:
      - pluginRef: prefix-cache-scorer
        weight: 0
      - pluginRef: slo-request-tracker
      - pluginRef: slo-scorer
      - pluginRef: max-score-picker
```

## Further Reading

- [Predicted Latency-Based Scheduling Guide](../../../guides/predicted-latency-based-scheduling/README.md) -- step-by-step deployment and validation walkthrough
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms) -- blog post with benchmarks and design rationale
- [EPP Architecture](../core/epp.md) -- details on the plugin pipeline and scoring system
