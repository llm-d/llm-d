# Prefill/Decode Disaggregation on Google TPU 7x

This guide demonstrates how to deploy `Qwen/Qwen3.5-397B-A17B-FP8` using prefill-decode (P/D) disaggregation on Google TPU 7x clusters. 

For a comprehensive overview of P/D disaggregation architecture, best practices, and benchmarking, please refer to the **[Unified P/D Disaggregation Guide](./README.md)**.

## Prerequisites

Before starting, ensure your cluster and environment are properly configured:

1. **TPU Topology:** Your GKE cluster must have TPU 7x nodes provisioned with a `2x2x1` topology (4 chips per node) to accommodate the model requirements.
   > [!NOTE]
   > **TPU7x Cores and Parallelism:** TPU7x has 2 cores per chip. You need to consider this when setting parallelism. For example, in `guides/pd-disaggregation/modelserver/tpu/vllm/patch-decode.yaml`, with 4 chips per pod, the tensor parallel size (`--tensor-parallel-size`) is set to `8`. 
2. Complete the **[Prerequisites](./README.md#prerequisites)** section in the main guide to clone the repository and install the Gateway API Inference Extension CRDs.
3. Set your environment variables, overriding the model name for Qwen 3.5:

```bash
export GAIE_VERSION=v1.5.0
export GUIDE_NAME="pd-disaggregation"
export NAMESPACE="llm-d-pd-disaggregation"
export MODEL_NAME="Qwen/Qwen3.5-397B-A17B-FP8"
```

## Installation Instructions

### 1. Deploy the llm-d Router

Deploy the router in either Standalone or Gateway mode by following the exact instructions in the **[Deploy the llm-d Router](./README.md#1-deploy-the-llm-d-router)** section of the main guide.

### 2. Deploy the TPU Model Server

Once the router is deployed, apply the Kustomize overlays specifically configured for TPU 7x and vLLM. This configuration sets up heterogeneous KV caches (HMA) and configures the TPU workers.

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/tpu/vllm/
```

*(Note: If you have monitoring enabled, you can optionally apply the monitoring components as described in the [main guide](./README.md#3-enable-monitoring-optional)).*

## Verification

Follow the **[Verification steps in the main guide](./README.md#verification)** to retrieve the proxy IP address. 

When sending your test request, ensure you use the correct TPU model name:

```bash
# Send a completion request to the TPU deployment
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "Qwen/Qwen3.5-397B-A17B-FP8",
    "prompt": "How are you today?"
    }' | jq
```

## Benchmarking

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a workload tailored for TPU 7x. For more details, refer to the [benchmark instructions doc](../../helpers/benchmark.md).

### 1. Prepare the Benchmarking Suite

Follow the **[Prepare the Benchmarking Suite](./README.md#1-prepare-the-benchmarking-suite)** section in the main guide to download the benchmark script and configure your environment.

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/pd-disaggregation/benchmark-templates/tpu_v7_qwen3_5.yaml"
```

### 3. Execute Benchmark

```bash
envsubst < tpu_v7_qwen3_5.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```
## Cleanup

To clean up your cluster, return to the **[Cleanup](./README.md#cleanup)** section of the unified guide.

## Benchmarking Report

The benchmark is running on 8 TPU 7x chips (2 pods with 2x2x1 topology).

<details>
<summary><b><i>Click</i></b> here to view the report from the above example</summary>

```yaml
metrics:
  latency:
    inter_token_latency:
      max: 0.11871303897351027
      mean: 0.03709993095523411
      min: 1.0403338819742203e-05
      p0p1: 0.013160184264648708
      p1: 0.02280808158684522
      p10: 0.029356164345517754
      p25: 0.030825229245238006
      p5: 0.02448836083058268
      p50: 0.03744487161748111
      p75: 0.03906837245449424
      p90: 0.039964481815695764
      p95: 0.041592727601528164
      p99: 0.11384667418431489
      p99p9: 0.11746114251948896
      units: s/token
    normalized_time_per_output_token:
      max: 2.7779783537300924
      mean: 0.2842568107979318
      min: 0.029594441250381756
      p0p1: 0.029735615131422925
      p1: 0.030888821579850418
      p10: 0.035099310792975305
      p25: 0.04077153328666373
      p5: 0.03269792121700837
      p50: 0.04940501823814711
      p75: 0.12363602356580884
      p90: 0.9653475074046894
      p95: 1.7841524277951812
      p99: 2.613900897125423
      p99p9: 2.763221654085458
      units: s/token
    request_latency:
      max: 48.89155744481832
      mean: 40.75879854830758
      min: 30.275113399140537
      p0p1: 30.419534279445653
      p1: 31.60522086889483
      p10: 34.29674377823248
      p25: 36.024264448089525
      p5: 33.0178924552165
      p50: 41.63587325881235
      p75: 45.15355795517098
      p90: 46.39733429183252
      p95: 47.46805788658094
      p99: 48.67747580135707
      p99p9: 48.878356086750514
      units: s
    time_per_output_token:
      max: 0.042322098327076674
      mean: 0.03709993095523411
      min: 0.028353755577427364
      p0p1: 0.028425141042895574
      p1: 0.02902252048457285
      p10: 0.031067102653969413
      p25: 0.03285369381762848
      p5: 0.030236626909822917
      p50: 0.037827638220733206
      p75: 0.04145547172083752
      p90: 0.04199908757545927
      p95: 0.042118943076252434
      p99: 0.04224829734961077
      p99p9: 0.042314721216826
      units: s/token
    time_to_first_token:
      max: 5.874797150027007
      mean: 2.7547412356943823
      min: 1.226183040998876
      p0p1: 1.2268008035453968
      p1: 1.2345757358893752
      p10: 1.598983020056039
      p25: 1.953679692815058
      p5: 1.280670134886168
      p50: 2.5601073873694986
      p75: 3.450429807882756
      p90: 4.046125307539478
      p95: 4.639469341677613
      p99: 5.70049525849987
      p99p9: 5.867744437836111
      units: s
  requests:
    failures: 0
    input_length:
      max: 1072.0
      mean: 1050.175
      min: 1036.0
      p0p1: 1036.0
      p1: 1036.38
      p10: 1041.0
      p25: 1046.0
      p5: 1039.0
      p50: 1050.0
      p75: 1055.0
      p90: 1059.0
      p95: 1060.05
      p99: 1062.0
      p99p9: 1070.8100000000002
      units: count
    output_length:
      max: 1041.0
      mean: 682.775
      min: 15.0
      p0p1: 15.119
      p1: 16.0
      p10: 43.300000000000004
      p25: 330.75
      p5: 21.95
      p50: 842.0
      p75: 1023.0
      p90: 1024.0
      p95: 1024.0
      p99: 1024.0
      p99p9: 1038.9770000000003
      units: count
    total: 120
  throughput:
    output_tokens_per_sec: 536.2990930576744
    requests_per_sec: 0.7854697273006106
    total_tokens_per_sec: 1361.179763925593
  time:
    duration: 117.41250333702192
scenario:
  load:
    args:
      api:
        headers: null
        streaming: true
        type: completion
      circuit_breakers: null
      data:
        input_distribution:
          max: 1024
          mean: 1024.0
          min: 1024
          std_dev: 0.0
          total_count: 121
        output_distribution:
          max: 1024
          mean: 1024.0
          min: 1024
          std_dev: 0.0
          total_count: 121
        path: null
        shared_prefix: null
        trace: null
        type: random
      load:
        circuit_breakers: []
        interval: 1.0
        lora_traffic_split: null
        num_workers: 100
        request_timeout: null
        stages:
        - concurrency_level: null
          duration: 120
          num_requests: null
          rate: 1.0
        sweep: null
        trace: null
        type: constant
        worker_max_concurrency: 100
        worker_max_tcp_connections: 2500
      metrics: null
      report:
        prometheus:
          per_stage: false
          summary: true
        request_lifecycle:
          per_adapter: true
          per_adapter_stage: false
          per_request: false
          per_stage: true
          percentiles:
          - 0.1
          - 1.0
          - 5.0
          - 10.0
          - 25.0
          - 50.0
          - 75.0
          - 90.0
          - 95.0
          - 99.0
          - 99.9
          summary: true
      server:
        api_key: null
        base_url: http://10.0.235.214
        cert_path: null
        ignore_eos: true
        key_path: null
        model_name: Qwen/Qwen3.5-397B-A17B-FP8
        type: vllm
      storage:
        google_cloud_storage: null
        local_storage:
          path: /requests/inference-perf_1777686654_random_1k_1k_isl_osl_tpu-v7-qwen3-5-pd
          report_file_prefix: null
        simple_storage_service: null
      tokenizer:
        pretrained_model_name_or_path: Qwen/Qwen3.5-397B-A17B-FP8
        token: null
        trust_remote_code: null
    metadata:
      stage: 0
    name: inference-perf
  model:
    name: unknown
version: '0.1'

```
