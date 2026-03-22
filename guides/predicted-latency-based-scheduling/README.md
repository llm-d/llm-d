# Experimental Feature: Predicted Latency based Load Balancing

## Overview

This experimental feature introduces **predicted latency based load balancing**, where scheduling decisions are guided by real-time predictions of request latency rather than only utilization metrics like queue depth or KV-cache utilization.

- **Problem:** Utilization-based load balancing misses some distinct characteristics of LLM workloads, leading to requests missing SLO targets or leads to overly conservative routing that wastes capacity.
- **Approach:** The Endpoint Picker (EPP) integrates with **in-pod latency predictor sidecars** that continuously learn from live traffic. These sidecars estimate **p90 TTFT** and **p90 TPOT** for each candidate pod given current load, prefix cache state, and request features.
- **Outcome:** The **SLO scorer** compares predictions against per-request SLOs and directs traffic to pods with some headroom. If none exist, requests are shed (priority < 0) or sent to a weighted pool favoring lower latency pods.

### Tradeoffs & Gaps

- **Homogeneous InferencePool**
  Current predictors assume that all model server pods are identical (same GPU type, model weights, and serving configuration). Heterogeneous pools are not yet modeled.

- **Scaling limits**
  Each prediction sidecar can sustain ~300 QPS on a c4-standard-192 Google cloud machine (**≈ 192 vCPUs, 720 GB RAM, Up to 100 Gbps network, Up to 200 Gbps aggregate throughput**). Because the EPP makes one prediction call per candidate pod, total prediction load grows with both **cluster QPS** and **pod count**. If traffic or pod count increases, prediction servers must be scaled horizontally.

- **Training mode**
  Only streaming workloads (set **"stream": "true"** in the request body as per openAI protocol) are supported.

- **Percentiles**
  The predictor currently estimates only **p90** TTFT and TPOT. Other percentiles (p95, p99) or a mix of percentiles are not yet available.

- **Prefill/Decode disaggregation**
  Current routing does **not support prefill/decode disaggregation** (where one pod performs prefill and another performs decode). Prediction and SLO scoring assume a pod executes the entire request lifecycle. Support for disaggregated serving is a **work in progress**.

- **Unvalidated against advanced inference features**
  Predictions have not yet been tested with advanced serving strategies such as LoRA adapters, speculative decoding, or beam search. Each of these may shift latency characteristics (e.g., speculative decoding may reduce TTFT but increase TPOT variance), and models may need to be extended to remain accurate in these contexts.

### What is Tested

This feature has been validated against the scenarios described in the [original design doc](https://docs.google.com/document/d/1q56wr3N5XGx0B21MzHu5oBsCiGi9VrbZAvyhP2VFG_c/edit?tab=t.0#heading=h.ob7j9esmcyd3) — including **short-prompt/long-completion**, **long-prompt/short-completion**, and **mixed workloads** — to compare baseline inference gateway routing versus prediction-based SLO routing. The benchmarking results are included in this doc.

This guide explains how to deploy EPP with latency predictor sidecars, configure profiles and scorers, and enable **SLO-aware routing** via headers.

---

## Prerequisites

- **Kubernetes cluster** with Gateway API CRDs installed
- **Helmfile** installed (<https://helmfile.readthedocs.io/>)
- **Helm** v3+ installed
- **kubectl** configured to access your cluster
- **Docker/BuildKit** (if building custom images)
- Access to container registry for EPP and latency predictor images

### Build EPP and Latency Predictor Images (Optional)

If you want to build custom images from the experimental branch:

1. **Clone & checkout the experimental branch**

    ```bash
    git clone https://github.com/kubernetes-sigs/gateway-api-inference-extension.git
    cd gateway-api-inference-extension
    git checkout slo-prediction-experimental
    ```

2. **Build EPP image**

    ```bash
    export IMG="<your-registry>/epp:slo-prediction-$(git rev-parse --short HEAD)"
    docker build -t "$IMG" -f Dockerfile .
    docker push "$IMG"
    ```

3. **Build latency predictor sidecars**
   Follow instructions at <https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/slo-prediction-experimental/latencypredictor-v1>

---

## Deployment

This guide uses helmfile to orchestrate deployment of:
- Infrastructure components (inference gateway)
- Gateway API Inference Extension (GAIE) with latency predictor sidecars
- Model servers (vLLM)
- HTTPRoute configuration

### File Structure

```
latencypredictor-based-scheduling/
├── helmfile.yaml.gotmpl         # Helmfile orchestration
├── gaie-latency/
│   └── values.yaml              # InferencePool & EPP configuration
├── ms-latency/
│   └── values.yaml              # Model server configuration
├── httproute.yaml               # HTTPRoute for non-GKE environments
└── httproute.gke.yaml           # HTTPRoute for GKE environments
```

### Deploy with Helmfile

1. **Review and customize configuration**
   - Edit `gaie-latency/values.yaml` to set your EPP and latency predictor images
   - Edit `ms-latency/values.yaml` to configure model servers
   - Update helmfile environment if needed (default: `istio`, other options: `kgateway`, `gke`, `agentgateway`)

2. **Deploy all components**

    ```bash
    helmfile sync
    ```

    This will deploy:
    - `infra-latency`: Inference gateway infrastructure
    - `gaie-latency`: InferencePool with EPP and latency predictor sidecars
    - `ms-latency`: Model servers (3 decode replicas, 1 prefill replica)

3. **Apply HTTPRoute**

    For non-GKE environments:
    ```bash
    kubectl apply -f httproute.yaml
    ```

    For GKE:
    ```bash
    kubectl apply -f httproute.gke.yaml
    ```

### Verify Deployment

1. **Check readiness**
   - Verify pod status: `kubectl get pods -n llm-d-latency` → all containers `Running/Ready`
   - Get gateway IP: `kubectl get gateway infra-latency-inference-gateway -n llm-d-latency`
   - Training sidecar health: `curl http://<pod-ip>:8000/readyz`
   - Prediction sidecar health: `curl http://<pod-ip>:8001/readyz`
   - EPP gRPC health: port `9003` (liveness/readiness probes)

2. **Send traffic**
   - **Baseline:** run requests using the **`default`** profile (no prediction headers).
   - **SLO-aware:** run requests with the **`slo`** profile and set
     `x-prediction-based-scheduling: true`, optionally adding SLO headers like `x-slo-ttft-ms` and `x-slo-tpot-ms`.

   Example request:

   ```bash
   curl -v $GW_IP/v1/completions \
     -H 'Content-Type: application/json' \
     -H 'x-prediction-based-scheduling: true' \
     -H 'x-slo-ttft-ms: 200' \
     -H 'x-slo-tpot-ms: 50' \
     -d '{
       "model": "meta-llama/Llama-3.1-8B-Instruct",
       "prompt": "what is the difference between Franz and Apache Kafka?",
       "max_tokens": 200,
       "temperature": 0,
       "stream_options": {"include_usage": "true"},
       "stream": "true"
     }'
   ```

   Example response (abridged SSE):

   ```text
   < HTTP/1.1 200 OK
   < content-type: text/event-stream; charset=utf-8
   ...
   data: {"choices":[{"index":0,"text":" Apache"}], "object":"text_completion", ...}
   data: {"choices":[{"index":0,"text":" Kafka"}],  "object":"text_completion", ...}
   ... (many streamed tokens) ...
   data: {
     "object":"text_completion",
     "usage": {
       "prompt_tokens": 12,
       "completion_tokens": 200,
       "total_tokens": 212,
       "ttft_ms": 59,
       "tpot_observations_ms": [9, 6],
       "avg_tpot_ms": 7.5,
       "predicted_ttft_ms": 273.23,
       "predicted_tpot_observations_ms": [176.22, 18.17],
       "avg_predicted_tpot_ms": 97.19
     }
   }
   data: [DONE]
   ```

   - The final SSE frame includes both **predictions and actuals** so you can validate accuracy (e.g., `predicted_ttft_ms` vs `ttft_ms`).
   - TPOTs are sampled every 200th token and surfaced in the arrays like `tpot_observations_ms`.

3. **Validate predictions in logs**

   Tail EPP logs at verbosity `-v=4`. For each request you should see:

   - **Profile selection**

     ```text
     msg:"Running profile handler, Pick profiles"
     plugin:"slo-aware-profile-handler/slo-aware-profile-handler"
     ```

   - **Candidate pods**

     ```text
     msg:"Before running scorer plugins"
     pods:[{... "pod_name":"...-5k7qr" ...}, {... "pod_name":"...-9lp5g" ...}]
     ```

   - **SLO scorer pod scores**

     ```text
     msg:"Pod score"
     scorer_type:"slo-scorer"
     pod_name:"vllm-llama3-8b-instruct-7b584dd595-9b4wt"
     score:0.82
     ```

   - **Final pick**

     ```text
     msg:"Picked endpoint"
     scorer_type:"slo-scorer"
     selected_pod:"vllm-llama3-8b-instruct-7b584dd595-9b4wt"
     ```

   These logs confirm:
   - The request entered the SLO-aware path.
   - All candidate pods were evaluated.
   - Scores reflect predicted headroom vs SLOs.
   - The final pod was chosen based on SLO scorer output.

4. **Confirm request shedding (optional)**

   If you send requests with **priority < 0** and no pod can meet both TTFT & TPOT SLOs, logs should show the request being **shed** instead of placed in the negative bucket.

---

## Configuration

Configuration is managed through Helm values files. The main configuration files are:
- `gaie-latency/values.yaml`: InferencePool, EPP, and latency predictor configuration
- `ms-latency/values.yaml`: Model server configuration

### InferencePool & EPP Configuration (`gaie-latency/values.yaml`)

Key configuration sections in `gaie-latency/values.yaml`:

#### EPP Image Configuration

```yaml
inferenceExtension:
  image:
    name: epp
    hub: quay.io/rh-ee-kapjain
    tag: slo-prediction-06bb5de8
    pullPolicy: Always
```

#### Latency Predictor Configuration

The latency predictor setup includes:

1. **Training Server** (port 8000):
   - Continuously retrains models from live traffic
   - Stores models in `/models` volume
   - Configuration in `gaie-latency/values.yaml:28-48`

2. **Prediction Servers** (starting at port 8001):
   - Serve predictions to EPP
   - Load models from `/server_models` volume
   - Configuration in `gaie-latency/values.yaml:50-65`

3. **EPP Environment Variables** (for headroom tuning):
   - Configuration in `gaie-latency/values.yaml:67-74`

Example latency predictor configuration:

```yaml
latencyPredictor:
  enabled: true
  trainingServer:
    image:
      hub: quay.io/rh-ee-kapjain
      name: latency-predictor-training
      tag: latency-predictor-06bb5de8
    port: 8000
    config:
      LATENCY_RETRAINING_INTERVAL_SEC: "1"
      LATENCY_MIN_SAMPLES_FOR_RETRAIN: "100"
      LATENCY_MODEL_TYPE: "xgboost"
      LATENCY_QUANTILE_ALPHA: "0.9"
  predictionServers:
    count: 1
    startPort: 8001
    image:
      hub: quay.io/rh-ee-kapjain
      name: latency-predictor-prediction
      tag: latency-predictor-06bb5de8
```

#### Plugins & Scheduling Profiles

The EPP plugins configuration is defined in `gaie-latency/values.yaml:76-105`:

```yaml
pluginsConfigFile: "slo-prediction-plugins.yaml"
pluginsCustomConfig:
  slo-prediction-plugins.yaml: |
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

**Plugin Descriptions:**
- `slo-request-tracker`: Captures per-request SLOs from headers
- `slo-scorer`: Uses predicted TTFT/TPOT to score pods based on SLO headroom
- `slo-aware-profile-handler`: Switches to `slo` profile when SLO headers are present
- `queue-scorer`, `kv-cache-utilization-scorer`, `prefix-cache-scorer`: Baseline scoring plugins

### Model Server Configuration (`ms-latency/values.yaml`)

The model server configuration includes:

- **Model**: Llama-3.1-8B-Instruct
- **Decode replicas**: 3 (for serving decode requests)
- **Prefill replicas**: 1 (for handling prefill)
- **Image**: `ghcr.io/llm-d/llm-d-inference-sim:v0.7.1` (simulation mode for testing)

Key configuration sections in `ms-latency/values.yaml`:

```yaml
modelArtifacts:
  uri: "hf://meta-llama/Llama-3.1-8B-Instruct"
  labels:
    llm-d.ai/inference-serving: "true"
    llm-d.ai/guide: "latencypredictor-based-scheduling"

decode:
  replicas: 3
  monitoring:
    podmonitor:
      enabled: true

prefill:
  replicas: 1
```

---

### Headroom Strategies

Headroom scoring parameters are configured in `gaie-latency/values.yaml:67-74`:

```yaml
latencyPredictor:
  eppEnv:
    LATENCY_MAX_SAMPLE_SIZE: "10000"
    NEG_HEADROOM_TTFT_WEIGHT: "0.5"
    NEG_HEADROOM_TPOT_WEIGHT: "0.5"
    HEADROOM_TTFT_WEIGHT: "0.5"
    HEADROOM_TPOT_WEIGHT: "0.5"
    HEADROOM_SELECTION_STRATEGY: "least"  # or "most"
    SLO_BUFFER_FACTOR: "1.1"
```

**Parameter descriptions:**
- `HEADROOM_SELECTION_STRATEGY`: `least` (compact, minimize pods used) or `most` (spread, distribute load)
- `HEADROOM_TTFT_WEIGHT` / `HEADROOM_TPOT_WEIGHT`: Blend weights for positive headroom scoring
- `NEG_HEADROOM_TTFT_WEIGHT` / `NEG_HEADROOM_TPOT_WEIGHT`: Blend weights for negative headroom (when SLOs cannot be met)
- `SLO_BUFFER_FACTOR`: Safety multiplier on TPOT SLOs (e.g., 1.1 = 10% buffer)
- `LATENCY_MAX_SAMPLE_SIZE`: Maximum number of latency samples to retain

---

### HTTPRoute Configuration

The HTTPRoute connects the inference gateway to the InferencePool:

**For non-GKE environments** (`httproute.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-d-latency
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: infra-latency-inference-gateway
  rules:
    - backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: gaie-latency
          port: 8000
```

The HTTPRoute:
- Routes traffic from the `infra-latency-inference-gateway` Gateway
- Directs requests to the `gaie-latency` InferencePool
- Sets infinite timeouts (`0s`) to allow long-running streaming requests

---

## Using Prediction-Based Scheduling

### Enable SLO-Aware Routing

Turn on prediction-based scheduling per request with the header:

```text
x-prediction-based-scheduling: true
```

**Behavior:**
- If **SLO headers are present** (`x-slo-ttft-ms`, `x-slo-tpot-ms`): Predictions are compared against thresholds, and requests are routed to pods with sufficient headroom
- If **no SLOs** are provided: Treated as SLO=0, routing to the lowest latency pod
- If **priority < 0** and **no pod can meet SLOs**: Request is **shed** (rejected) instead of being placed in the negative bucket

### Current Limitations

- **Percentile**: Only **p90** supported
- **Training mode**: Only **streaming mode** (`"stream": "true"`) supported
- **TPOT sampling**: For observability, every 200th token is logged and compared with predictions

---

## Cleanup

To remove all resources created by this guide:

1. **Delete HTTPRoute**

    ```bash
    kubectl delete -f httproute.yaml
    # or for GKE:
    kubectl delete -f httproute.gke.yaml
    ```

2. **Delete Helm releases**

    ```bash
    helmfile destroy
    ```

    This will remove:
    - `ms-latency`: Model servers
    - `gaie-latency`: InferencePool and EPP with latency predictors
    - `infra-latency`: Inference gateway infrastructure

3. **Verify cleanup**

    ```bash
    kubectl get pods -n llm-d-latency
    kubectl get inferencepools -n llm-d-latency
    kubectl get gateways -n llm-d-latency
    ```
