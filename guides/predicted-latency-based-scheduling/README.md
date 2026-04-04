# Predicted Latency based Load Balancing

## Overview

This feature introduces **predicted latency based load balancing**, where scheduling decisions are guided by real-time predictions of request latency rather than only utilization metrics like queue depth or KV-cache utilization.

- **Problem:** Utilization-based load balancing misses some distinct characteristics of LLM workloads, leading to requests missing SLO targets or leads to overly conservative routing that wastes capacity.
- **Approach:** The Endpoint Picker (EPP) integrates with **in-pod latency predictor sidecars** that continuously learn from live traffic. These sidecars estimate **p90 TTFT** and **p90 TPOT** for each candidate pod given current load, prefix cache state, and request features.
- **Outcome:** The **predicted-latency-scorer** compares predictions against per-request SLOs and directs traffic to pods with some headroom. If none exist, requests are shed (priority < 0) or sent to a weighted pool favoring lower latency pods.

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

This guide explains how to deploy EPP with latency predictor sidecars using vLLM simulator, configure profiles and scorers, and enable **SLO-aware routing** via headers.

---

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
- Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-precise # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

** Required if using hf models 
- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.

- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)

## Installation


## Deployment

This guide uses helmfile to orchestrate deployment of:
- Infrastructure components (inference gateway)
- Gateway API Inference Extension (GAIE) with latency predictor sidecars
- Model servers (vLLM)
- HTTPRoute configuration


### File Structure

```
predicted-latency-based-scheduling/
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
```bash
   helm list -n ${NAMESPACE}
      NAME         	NAMESPACE	REVISION	UPDATED                             	STATUS  	CHART                    	APP VERSION
      gaie-latency 	kapjain  	1       	2026-04-04 13:08:15.82823 -0400 EDT 	deployed	inferencepool-v1.4.0     	v1.4.0     
      infra-latency	kapjain  	1       	2026-04-04 13:08:13.731244 -0400 EDT	deployed	llm-d-infra-v1.4.0       	v0.4.0     
      ms-latency   	kapjain  	1       	2026-04-04 13:08:21.003621 -0400 EDT	deployed	llm-d-modelservice-v0.4.7	v0.4.0
```

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

```bash
oc get all -n $NAMESPACE
NAME                                                         READY   STATUS    RESTARTS   AGE
pod/gaie-latency-epp-777bd56cf4-rh62m                        3/3     Running   0          56m
pod/infra-latency-inference-gateway-istio-79f75c6575-cdk5w   1/1     Running   0          56m
pod/ms-latency-llm-d-modelservice-decode-cf4fc478c-7cvqb     2/2     Running   0          56m
pod/ms-latency-llm-d-modelservice-decode-cf4fc478c-p5k8t     2/2     Running   0          56m
pod/ms-latency-llm-d-modelservice-decode-cf4fc478c-tld42     2/2     Running   0          56m

NAME                                            TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)               AGE
service/gaie-latency-epp                        ClusterIP   172.30.190.255   <none>        9002/TCP,9090/TCP     56m
service/gaie-latency-ip-c98a576e                ClusterIP   None             <none>        54321/TCP,54322/TCP   56m
service/infra-latency-inference-gateway-istio   ClusterIP   172.30.127.196   <none>        15021/TCP,80/TCP      56m

NAME                                                    READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-latency-epp                        1/1     1            1           56m
deployment.apps/infra-latency-inference-gateway-istio   1/1     1            1           56m
deployment.apps/ms-latency-llm-d-modelservice-decode    3/3     3            3           56m

NAME                                                               DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-latency-epp-777bd56cf4                        1         1         1       56m
replicaset.apps/infra-latency-inference-gateway-istio-79f75c6575   1         1         1       56m
replicaset.apps/ms-latency-llm-d-modelservice-decode-cf4fc478c     3         3         3       56m
```

2. **Send traffic**
   - **Baseline:** run requests using the **`default`** profile (no prediction headers).
   - **SLO-aware:** run requests with the **`slo`** profile and set
     `x-prediction-based-scheduling: true`, optionally adding SLO headers like `x-slo-ttft-ms` and `x-slo-tpot-ms`.

   Example request:

   ```bash
   kubectl port-forward -n ${NAMESPACE} service/infra-kv-events-inference-gateway-istio 8000:80
   curl -v localhost:8000/v1/completions \
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

3. **Validate predictions in logs**

1. Check the inference-scheduler's prefix-cache-scorer's scores with the following command:

```bash
kubectl logs -l inferencepool=gaie-latency-epp --all-containers=true -n ${NAMESPACE} --tail 100 | grep "Calculated score" | grep "predicted-latency-scorer/predicted-latency-scorer
```

You should see output similar to:

```json
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-p5k8t-rank-1","namespace":"kapjain"},"score":1}
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-7cvqb-rank-1","namespace":"kapjain"},"score":0}
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-tld42-rank-0","namespace":"kapjain"},"score":0}
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-7cvqb-rank-0","namespace":"kapjain"},"score":0}
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-p5k8t-rank-0","namespace":"kapjain"},"score":0}
{"level":"Level(-4)","ts":"2026-04-04T20:07:41Z","caller":"scheduling/scheduler_profile.go:166","msg":"Calculated score","x-request-id":"d5b1ba48-16b4-474b-abb3-a2493456f67e","objectiveKey":"","incomingModelName":"llama-3-1-8b-instruct","targetModelName":"llama-3-1-8b-instruct","priority":0,"plugin":"predicted-latency-scorer/predicted-latency-scorer","endpoint":{"name":"ms-latency-llm-d-modelservice-decode-cf4fc478c-tld42-rank-1","namespace":"kapjain"},"score":0}
```
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
    name: llm-d-inference-scheduler
    hub: quay.io/rh_ee_rsaini
    tag: slo-pd-experimental
```

#### Latency Predictor Configuration

The latency predictor setup includes:

1. **Training Server** (port 8000):
   - Continuously retrains models from live traffic
   - Stores models in `/models` volume

```yaml
     trainingServer:
     image:
       hub: us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension
       name: latency-training-server
       tag: main
       pullPolicy: Always
     port: 8000
     volumeSize: "20Gi"
     config:
       LATENCY_RETRAINING_INTERVAL_SEC: "10"
       LATENCY_MIN_SAMPLES_FOR_RETRAIN: "100"
       LATENCY_TTFT_MODEL_PATH: "/models/ttft.joblib"
       LATENCY_TPOT_MODEL_PATH: "/models/tpot.joblib"
       LATENCY_TTFT_SCALER_PATH: "/models/ttft_scaler.joblib"
       LATENCY_TPOT_SCALER_PATH: "/models/tpot_scaler.joblib"
       LATENCY_MODEL_TYPE: "xgboost"
       LATENCY_MAX_TRAINING_DATA_SIZE_PER_BUCKET: "500"
       LATENCY_OBJECTIVE_TYPE: "mean"
 ```

2. **Prediction Servers** (starting at port 8001):
   - Serve predictions to EPP
   - Load models from `/server_models` volume
   - 
```yaml  
   predictionServers:
     startPort: 8001
     image:
       hub: us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension
       name: latency-prediction-server
       tag: main
       pullPolicy: Always
     volumeSize: "10Gi"
     config:
       LATENCY_MODEL_TYPE: "xgboost"
       PREDICT_HOST: "0.0.0.0"
       LOCAL_TTFT_MODEL_PATH: "/server_models/ttft.joblib"
       LOCAL_TPOT_MODEL_PATH: "/server_models/tpot.joblib"
       LOCAL_TTFT_SCALER_PATH: "/server_models/ttft_scaler.joblib"
       LOCAL_TPOT_SCALER_PATH: "/server_models/tpot_scaler.joblib"
       UVICORN_WORKERS: "28"
       OMP_NUM_THREADS: "1"
       MODEL_SYNC_INTERVAL_SEC: "30"
       LATENCY_OBJECTIVE_TYPE: "mean" 
```

3. **EPP Environment Variables** (for headroom tuning):


```yaml
inferenceExtension:
  replicas: 1
  flags:
    v: 4  # log verbosity
  latencyPredictor:
    enabled: true
  image:
    name: llm-d-inference-scheduler
    hub: quay.io/rh_ee_rsaini
    tag: slo-pd-experimental
    pullPolicy: Always
  extProcPort: 9002
 ```

#### Plugins & Scheduling Profiles

The EPP plugins configuration is defined in `gaie-latency/values.yaml:76-105`:

```yaml
  pluginsConfigFile: "slo-prediction-plugins.yaml"
  pluginsCustomConfig:  # THIS CONFIG NEEDS TO BE CHECKED FOR INF SCHEDULER NEW IMAGE
    slo-prediction-plugins.yaml: |
      # ALWAYS DO PD IN THIS EXAMPLE (THRESHOLD 0)
      apiVersion: inference.networking.x-k8s.io/v1alpha1
      kind: EndpointPickerConfig
      featureGates:
      - prepareDataPlugins
      plugins:
      - type: prefix-cache-scorer
        parameters:
          maxPrefixBlocksToMatch: 256
          lruCapacityPerServer: 31250
      - type: predicted-latency-scorer
        parameters:
          sloBufferFactor: 1.0
          headroomSelectionStrategy: "least"
          samplingMean: 50
          maxSampledTokens: 10
      - type: max-score-picker
      schedulingProfiles:
      - name: default
        plugins:
        - pluginRef: prefix-cache-scorer
        - pluginRef: predicted-latency-scorer
        - pluginRef: max-score-picker
```

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
