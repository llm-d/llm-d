# Well-lit Path: Intelligent Inference Scheduling

## Overview

This guide deploys the recommended out of the box [scheduling configuration](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md) for most vLLM deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing. This can be run on two GPUs that can load [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B).

This profile defaults to the approximate prefix cache aware scorer, which only observes request traffic to predict prefix cache locality. The [precise prefix cache aware routing feature](../precise-prefix-cache-aware) improves hit rate by introspecting the vLLM instances for cache entries and will become the default in a future release.

## Hardware Requirements

This example out of the box uses 16 GPUs (8 replicas x 2 GPUs each) of any supported kind:

- **NVIDIA GPUs**: Any NVIDIA GPU (support determined by the inferencing image used)
- **AMD GPUs**: Any AMD GPU (support determined by the inferencing image used)
- **Intel XPU/GPUs**: Intel Data Center GPU Max 1550 or compatible Intel XPU device
- **Intel Gaudi (HPU)**: Gaudi 1, Gaudi 2, or Gaudi 3 with DRA support
- **TPUs**: Google Cloud TPUs (when using GKE TPU configuration)

**Using fewer accelerators**: Fewer accelerators can be used by modifying the `values.yaml` corresponding to your deployment. For example, to use only 2 GPUs with the default NVIDIA GPU deployment, update `replicas: 2` in [ms-inference-scheduling/values.yaml](./ms-inference-scheduling/values.yaml#L17-L22).

**Alternative CPU Deployment**: For CPU-only deployment (no GPUs required), see the [Hardware Backends](#hardware-backends) section for CPU-specific deployment instructions. CPU deployment requires Intel/AMD CPUs with 64 cores and 64GB RAM per replica.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../prereq/infrastructure)
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
- Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-inference-scheduler # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md)

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-inference-scheduler` in this example.

**_IMPORTANT:_** When using long namespace names (like `llm-d-inference-scheduler`), the generated pod hostnames may become too long and cause issues due to Linux hostname length limitations (typically 64 characters maximum). It's recommended to use shorter namespace names (like `llm-d`) and set `RELEASE_NAME_POSTFIX` to generate shorter hostnames and avoid potential networking or vLLM startup problems.

### Deploy

```bash
cd guides/inference-scheduling
```

<!-- TABS:START -->
<!-- TAB:GPU deployment  -->

#### GPU deployment

```bash
helmfile apply -n ${NAMESPACE}
```

<!-- TAB:CPU deployment  -->

#### CPU-only deployment

```bash
helmfile apply -e cpu -n ${NAMESPACE}
```

<!-- TABS:END -->

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=inference-scheduling-2 helmfile apply -n ${NAMESPACE}`

### Gateway and Hardware Options

#### Gateway Options

**_NOTE:_** This uses Istio as the default gateway provider, see [Gateway Options](#gateway-options) for installing with a specific provider.

To specify your gateway choice you can use the `-e <gateway option>` flag, ex:

```bash
helmfile apply -e kgateway -n ${NAMESPACE}
```

For DigitalOcean Kubernetes Service (DOKS):

```bash
helmfile apply -e digitalocean -n ${NAMESPACE}
```

 **_NOTE:_** DigitalOcean deployment uses public Qwen/Qwen3-0.6B model (no HuggingFace token required) and is optimized for DOKS GPU nodes with automatic tolerations and node selectors. Gateway API v1 compatibility fixes are automatically included.

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../docs/customizing-your-gateway.md).

#### Hardware Backends

Currently in the `inference-scheduling` example we support configurations for `amd`, `xpu`, `tpu`, `cpu`, `hpu` (Intel Gaudi) and `cuda` GPUs. By default we use modelserver values supporting `cuda` GPUs, but to deploy on one of the other hardware backends you may use:

```bash
helmfile apply -e amd  -n ${NAMESPACE} # targets istio as gateway provider with AMD GPU hardware
helmfile apply -e xpu  -n ${NAMESPACE} # targets istio as gateway provider with XPU hardware
# or
helmfile apply -e hpu  -n ${NAMESPACE} # targets istio as gateway provider with Intel Gaudi (HPU) hardware
# or
helmfile apply -e gke_tpu  -n ${NAMESPACE} # targets GKE externally managed as gateway provider with TPU hardware
# or
helmfile apply -e cpu  -n ${NAMESPACE} # targets istio as gateway provider with CPU hardware
```

##### Intel XPU Configuration

For Intel XPU deployments, the `values_xpu.yaml` uses Dynamic Resource Allocation (DRA) with a unified Intel accelerator configuration:

```yaml
# For Intel GPUs (supports both i915 and xe drivers):
accelerator:
  type: intel
  dra: true
```

**Note:** The unified `intel` type works with both Intel Data Center GPU Max 1550 (i915 driver) and Intel BMG GPUs (Battlemage G21, xe driver). DRA automatically handles driver selection.

**Note for Intel Gaudi (HPU) deployments:** Intel Gaudi uses Dynamic Resource Allocation (DRA) support. Ensure you have the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) installed on your cluster. See [Accelerator documentation](../../docs/accelerators/README.md#dynamic-resource-allocation) for setup details.

##### CPU Inferencing

This case expects using 4th Gen Intel Xeon processors (Sapphire Rapids) or later.

### Install HTTPRoute When Using Gateway option

Follow provider specific instructions for installing HTTPRoute.

**_IMPORTANT:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, you **must** update the HTTPRoute file to match your custom release names before applying it. The HTTPRoute references the Gateway and InferencePool names which include the release name postfix.

For example, if you set `RELEASE_NAME_POSTFIX=my-custom`, you need to update the HTTPRoute:

```bash
# Update the HTTPRoute to match your release names
sed -e "s/infra-inference-scheduling-inference-gateway/infra-my-custom-inference-gateway/g" \
    -e "s/gaie-inference-scheduling/gaie-my-custom/g" \
    httproute.yaml > httproute-custom.yaml

# Then apply the customized HTTPRoute
kubectl apply -f httproute-custom.yaml -n ${NAMESPACE}
```

#### Install for "kgateway" or "istio"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

#### Install for "gke"

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

#### Install for "digitalocean"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

## Verify the Installation

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME                        NAMESPACE                   REVISION  UPDATED                                 STATUS      CHART                       APP VERSION
gaie-inference-scheduling   llm-d-inference-scheduler   1         2026-01-26 15:11:26.506854 +0200 IST    deployed    inferencepool-v1.3.0        v1.3.0
infra-inference-scheduling  llm-d-inference-scheduler   1         2026-01-26 15:11:21.008163 +0200 IST    deployed    llm-d-infra-v1.3.6          v0.3.0
ms-inference-scheduling     llm-d-inference-scheduler   1         2026-01-26 15:11:39.385111 +0200 IST    deployed    llm-d-modelservice-v0.4.5   v0.4.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                                  READY   STATUS    RESTARTS   AGE
pod/gaie-inference-scheduling-epp-59c5f64d7b-b5j2d                    1/1     Running   0          36m
pod/infra-inference-scheduling-inference-gateway-istio-55fd84cnjzfv   1/1     Running   0          36m
pod/llmdbench-harness-launcher                                        1/1     Running   0          2m43s
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c8795szd   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87cdntk   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87cnxxq   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87fvtjf   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87jqt27   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87kwxc6   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87rld4t   1/1     Running   0          35m
pod/ms-inference-scheduling-llm-d-modelservice-decode-866b7c87xvbmp   1/1     Running   0          35m

NAME                                                         TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)             AGE
service/gaie-inference-scheduling-epp                        ClusterIP   172.30.240.45    <none>        9002/TCP,9090/TCP   36m
service/gaie-inference-scheduling-ip-18c12339                ClusterIP   None             <none>        54321/TCP           36m
service/infra-inference-scheduling-inference-gateway-istio   ClusterIP   172.30.28.163    <none>        15021/TCP,80/TCP    36m

NAME                                                                 READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-inference-scheduling-epp                        1/1     1            1           36m
deployment.apps/infra-inference-scheduling-inference-gateway-istio   1/1     1            1           36m
deployment.apps/ms-inference-scheduling-llm-d-modelservice-decode    8/8     8            8           35m

NAME                                                                            DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-inference-scheduling-epp-59c5f64d7b                        1         1         1       36m
replicaset.apps/infra-inference-scheduling-inference-gateway-istio-55fd84c7fd   1         1         1       36m
replicaset.apps/ms-inference-scheduling-llm-d-modelservice-decode-866b7c8768    8         8         8       35m
```

## Using the stack

For instructions on getting started making inference requests see [our docs](../../docs/getting-started-inferencing.md)

## Benchmarking

To run benchmarks against the installed llm-d stack, you need [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh), a template file from [guides/benchmark](../benchmark/), and a Persistent Volume Claim (PVC) to store the results. Follow the instructions in the [benchmark doc](../benchmark/README.md).

### Example

This example uses [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh) with the template [inference_scheduling_guide_template.yaml](../benchmark/inference_scheduling_guide_template.yaml).

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a shared prefix synthetic workload named `shared_prefix_synthetic`. This workload runs several stages with different rates. The results will be stored on the provided PVC, accessible through the `llmdbench-harness-launcher` pod. Each experiment is saved under the `requests` folder, e.g.,/`requests/inference-perf_<experiment ID>_shared_prefix_synthetic_inference-scheduling_<model name>` folder.

Several results files will be created (see [Benchmark doc](../benchmark/README.md)), including a yaml file in a "standard" benchmark report format (see [Benchmark Report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md)).

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  select f in $(
      curl -s https://api.github.com/repos/llm-d/llm-d/contents/guides/benchmark?ref=main |
      sed -n '/[[:space:]]*"name":[[:space:]][[:space:]]*"\(inference_scheduling.*\_template\.yaml\)".*/ s//\1/p'
    ); do
    curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/benchmark/$f"
    break
  done
  ```

Choose the `inference_scheduling_guide_template.yaml` template, then run:

  ```bash
  export NAMESPACE=llm-d-inference-scheduler     # replace with your namespace
  export BENCHMARK_PVC=workload-pvc   # replace with your PVC name
  export GATEWAY_SVC=infra-inference-scheduling-inference-gateway-istio  # replace with your exact service name
  envsubst < inference_scheduling_guide_template.yaml > config.yaml
  ```

Edit `config.yaml` if further customization is needed, and then run the command

  ```bash
  ./run_only.sh -c config.yaml
  ```

The output will show the progress of the `inference-perf` benchmark as it runs
<details>
<summary><b><i>Click</i></b> here to view the expected output</summary>

  ```text
  ...
  2026-01-14 12:58:15,472 - inference_perf.client.filestorage.local - INFO - Report files will be stored at: /requests/inference-perf_1768395442_shared_prefix_synthetic_inference-scheduling-Qwen3-0.6B
  2026-01-14 12:58:18,414 - inference_perf.loadgen.load_generator - INFO - Stage 0 - run started
  Stage 0 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.06s/it]
  2026-01-14 12:59:10,503 - inference_perf.loadgen.load_generator - INFO - Stage 0 - run completed
  2026-01-14 12:59:11,504 - inference_perf.loadgen.load_generator - INFO - Stage 1 - run started
  Stage 1 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.05s/it]
  2026-01-14 13:00:03,566 - inference_perf.loadgen.load_generator - INFO - Stage 1 - run completed
  2026-01-14 13:00:04,569 - inference_perf.loadgen.load_generator - INFO - Stage 2 - run started
  Stage 2 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.05s/it]
  2026-01-14 13:00:56,620 - inference_perf.loadgen.load_generator - INFO - Stage 2 - run completed
  Stage 3 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-14 13:00:57,621 - inference_perf.loadgen.load_generator - INFO - Stage 3 - run started
  Stage 3 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.14s/it]  2026-01-14 13:01:49,675 - inference_perf.loadgen.load_generator - INFO - Stage 3 - run completed
  Stage 3 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.05s/it]
  2026-01-14 13:01:50,677 - inference_perf.loadgen.load_generator - INFO - Stage 4 - run started
  Stage 4 progress:  98%|█████████▊| 0.975/1.0 [00:51<00:01, 53.81s/it]2026-01-14 13:02:42,726 - inference_perf.loadgen.load_generator - INFO - Stage 4 - run completed
  Stage 4 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.05s/it]
  2026-01-14 13:02:43,727 - inference_perf.loadgen.load_generator - INFO - Stage 5 - run started
  Stage 5 progress:  98%|█████████▊| 0.976/1.0 [00:51<00:01, 47.18s/it]             2026-01-14 13:03:35,770 - inference_perf.loadgen.load_generator - INFO - Stage 5 - run completed
  Stage 5 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.04s/it]
  2026-01-14 13:03:36,771 - inference_perf.loadgen.load_generator - INFO - Stage 6 - run started
  Stage 6 progress: 100%|██████████| 1.0/1.0 [00:52<00:00, 52.05s/it]
  2026-01-14 13:04:28,826 - inference_perf.loadgen.load_generator - INFO - Stage 6 - run completed
  2026-01-14 13:04:29,932 - inference_perf.reportgen.base - INFO - Generating Reports...
  ...
  ```

</details>

### Benchmarking Report

There is a report for each stage.
<details>
<summary><b><i>Click</i></b> here to view the report for `rate=10` from the above example</summary>

  ```yaml
  metrics:
    latency:
      inter_token_latency:
        max: 0.5279842139862012
        mean: 0.023472589247039724
        min: 5.54401776753366e-06
        p0p1: 2.969687865697779e-05
        p1: 0.01570920992817264
        p10: 0.017796951622585766
        p25: 0.019922889761801343
        p5: 0.01697171464911662
        p50: 0.02313095549470745
        p75: 0.024240262260718737
        p90: 0.025133388102403842
        p95: 0.02772743094828911
        p99: 0.055353467414679496
        p99p9: 0.18073146573209703
        units: s/token
      normalized_time_per_output_token:
        max: 0.7521504626874957
        mean: 0.05686474655003883
        min: 0.01698542306901072
        p0p1: 0.01705017091645236
        p1: 0.017788033250498277
        p10: 0.020831146772993095
        p25: 0.02294853476344245
        p5: 0.019549211757198662
        p50: 0.024393047083762623
        p75: 0.02581844833641027
        p90: 0.03438874353119622
        p95: 0.17620685523326504
        p99: 0.7340219901647014
        p99p9: 0.7513766314058212
        units: s/token
      request_latency:
        max: 28.373117309005465
        mean: 23.649843642341583
        min: 16.98542306901072
        p0p1: 17.03639152829966
        p1: 17.367577876535652
        p10: 20.45322390751098
        p25: 22.20301700950222
        p5: 18.32161474993918
        p50: 23.907766903503216
        p75: 25.211236919509247
        p90: 26.957327539619293
        p95: 27.74618222430872
        p99: 28.286736061605623
        p99p9: 28.360666843361745
        units: s
      time_per_output_token:
        max: 0.02817760463198647
        mean: 0.02347258924703972
        min: 0.016891268502979073
        p0p1: 0.01694094809678159
        p1: 0.017275552588361897
        p10: 0.020236119398896697
        p25: 0.021978421900232206
        p5: 0.018211736758588812
        p50: 0.02373887161251332
        p75: 0.024932539490495398
        p90: 0.026851010997311093
        p95: 0.027605408759595593
        p99: 0.028058832576685237
        p99p9: 0.028157355884088523
        units: s/token
      time_to_first_token:
        max: 0.5789424130052794
        mean: 0.14620283814088908
        min: 0.05166479598847218
        p0p1: 0.05235437456815271
        p1: 0.05636055824958021
        p10: 0.062016059117740954
        p25: 0.0753971867452492
        p5: 0.05930683680344373
        p50: 0.136047175998101
        p75: 0.1975146289987606
        p90: 0.22555761661496943
        p95: 0.2796898997810785
        p99: 0.39144611745723484
        p99p9: 0.5504729018774547
        units: s
    requests:
      failures: 0
      input_length:
        max: 7665.0
        mean: 7577.135
        min: 7503.0
        p0p1: 7503.0
        p1: 7508.94
        p10: 7535.0
        p25: 7552.0
        p5: 7526.8
        p50: 7576.5
        p75: 7601.0
        p90: 7617.0
        p95: 7626.05
        p99: 7650.01
        p99p9: 7662.214
        units: count
      output_length:
        max: 1002.0
        mean: 911.31
        min: 32.0
        p0p1: 32.0
        p1: 32.0
        p10: 762.6000000000006
        p25: 991.0
        p5: 159.15
        p50: 997.0
        p75: 1000.0
        p90: 1000.0
        p95: 1000.0
        p99: 1001.0
        p99p9: 1001.801
        units: count
      total: 200
    throughput:
      output_tokens_per_sec: 4023.797460896292
      requests_per_sec: 4.415399217496013
      total_tokens_per_sec: 37479.873410757944
    time:
      duration: 20.956964999990305
  scenario:
    load:
      args:
        api:
          headers: null
          streaming: true
          type: completion
        circuit_breakers: null
        data:
          input_distribution: null
          output_distribution: null
          path: null
          shared_prefix:
            enable_multi_turn_chat: false
            num_groups: 150
            num_prompts_per_group: 5
            output_len: 1000
            question_len: 1200
            system_prompt_len: 6000
          trace: null
          type: shared_prefix
        load:
          circuit_breakers: []
          interval: 1.0
          num_workers: 224
          request_timeout: null
          stages:
          - concurrency_level: null
            duration: 50
            num_requests: null
            rate: 15.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 3.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 10.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 15.0
          - concurrency_level: null
            duration: 38
            num_requests: null
            rate: 20.0
          - concurrency_level: null
            duration: 34
            num_requests: null
            rate: 22.0
          - concurrency_level: null
            duration: 30
            num_requests: null
            rate: 25.0
          - concurrency_level: null
            duration: 25
            num_requests: null
            rate: 30.0
          - concurrency_level: null
            duration: 21
            num_requests: null
            rate: 35.0
          - concurrency_level: null
            duration: 38
            num_requests: null
            rate: 40.0
          - concurrency_level: null
            duration: 36
            num_requests: null
            rate: 43.0
          - concurrency_level: null
            duration: 33
            num_requests: null
            rate: 46.0
          - concurrency_level: null
            duration: 30
            num_requests: null
            rate: 49.0
          - concurrency_level: null
            duration: 29
            num_requests: null
            rate: 52.0
          - concurrency_level: null
            duration: 27
            num_requests: null
            rate: 55.0
          - concurrency_level: null
            duration: 26
            num_requests: null
            rate: 57.0
          - concurrency_level: null
            duration: 25
            num_requests: null
            rate: 60.0
          sweep: null
          trace: null
          type: poisson
          worker_max_concurrency: 100
          worker_max_tcp_connections: 2500
        metrics: null
        report:
          prometheus:
            per_stage: false
            summary: true
          request_lifecycle:
            per_request: true
            per_stage: true
            summary: true
        server:
          api_key: null
          base_url: http://infra-inference-scheduling-inference-gateway-istio.dpikus-intel-inf.svc.cluster.local:80
          ignore_eos: true
          model_name: Qwen/Qwen3-32B
          type: vllm
        storage:
          google_cloud_storage: null
          local_storage:
            path: /requests/inference-perf_1769435052_Shared_prefix_inf-scheduling-guide-Qwen3-32B
            report_file_prefix: null
          simple_storage_service: null
        tokenizer:
          pretrained_model_name_or_path: Qwen/Qwen3-32B
          token: null
          trust_remote_code: null
      metadata:
        stage: 2
      name: inference-perf
    model:
      name: unknown
  version: '0.1'
  ```

</details>

### Comparing LLM-d scheduling to a simple kubernetes service

We examine the overall behavior of the entire workload of the example above, using the `summary_lifecycle_metrics.json` produced by
`inference-perf`.
For comparison, we ran the same workload on a k8s service endpoint that directly uses the vLLM pods as backends.

- **Throughput**: Requests/sec 38.9% ; Output tokens/sec 38.8%
- **Latency**: TTFT (mean) -97.1% ; E2E request latency (mean) -31.2%
- **Per-token speed**: Time per output token (mean) 63.8% (slower)

| Metric                                                           | k8s       | llmd      | Δ (llmd - k8s)   | Δ% vs k8s  |
|:-----------------------------------------------------------------|:----------|:----------|:-----------------|:-----------|
| Requests/sec                                                     | 5.1038    | 7.0906    | 1.9868           | 38.9%      |
| Input tokens/sec                                                 | 38,688.28 | 53,751.21 | 15,062.92        | 38.9%      |
| Output tokens/sec                                                | 4,787.09  | 6,644.34  | 1,857.25         | 38.8%      |
| Total tokens/sec                                                 | 43,475.37 | 60,395.55 | 16,920.17        | 38.9%      |
| Approx. gen speed (1/mean time_per_output_token) [tok/s/request] | 19.778    | 12.072    | -7.7064          | -39.0%     |
| Request latency (s)                                              | 107.87    | 81.811    | -26.06           | -24.2%     |
| TTFT (s)                                                         | 55.968    | 0.357     | -55.61           | -99.4%     |
| Time/output token (ms)                                           | 52.91     | 79.24     | +0.02633         | +49.8%     |
| Inter-token latency (ms)                                         | 32.01     | 51.32     | +0.01930         | +60.3%     |

<!--
#### More

| Metric                                                           | k8s       | llmd      | Δ (llmd - k8s)   | Δ% vs k8s   |
|:-----------------------------------------------------------------|:----------|:----------|:-----------------|:------------|
| Request latency median (s)                                       | 107.87    | 81.811    | -26.060          | -24.2%      |
| Request latency mean (s)                                         | 123.49    | 84.998    | -38.491          | -31.2%      |
| Request latency min (s)                                          | 16.960    | 15.934    | -1.0258          | -6.0%       |
| Request latency max (s)                                          | 270.43    | 172.03    | -98.406          | -36.4%      |
| TTFT median (s)                                                  | 55.968    | 0.357006  | -55.611          | -99.4%      |
| TTFT mean (s)                                                    | 72.899    | 2.1319    | -70.767          | -97.1%      |
| TTFT min (s)                                                     | 0.059361  | 0.046948  | -0.012413        | -20.9%      |
| TTFT max (s)                                                     | 252.83    | 85.883    | -166.94          | -66.0%      |
| Inter-token latency median (s/token)                             | 0.032011  | 0.051315  | 0.019305         | 60.3%       |
| Inter-token latency mean (s/token)                               | 0.050560  | 0.082836  | 0.032276         | 63.8%       |
| Inter-token latency min (s/token)                                | 0.000006  | 0.000004  | -0.000001        | -22.0%      |
| Inter-token latency max (s/token)                                | 36.771    | 133.14    | 96.373           | 262.1%      |
| Time per output token median (s/token)                           | 0.052911  | 0.079237  | 0.026326         | 49.8%       |
| Time per output token mean (s/token)                             | 0.050560  | 0.082836  | 0.032276         | 63.8%       |
| Time per output token min (s/token)                              | 0.016120  | 0.015833  | -0.000288        | -1.8%       |
| Time per output token max (s/token)                              | 0.091111  | 0.171244  | 0.080133         | 88.0%       |
-->

## Cleanup

To remove the deployment:

```bash
# From examples/inference-scheduling
helmfile destroy -n ${NAMESPACE}

# Or uninstall manually
helm uninstall infra-inference-scheduling -n ${NAMESPACE} --ignore-not-found
helm uninstall gaie-inference-scheduling -n ${NAMESPACE}
helm uninstall ms-inference-scheduling -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

### Cleanup HTTPRoute when using Gateway option

Follow provider specific instructions for deleting HTTPRoute.

#### Cleanup for "kgateway" or "istio"

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```

#### Cleanup for "gke"

```bash
kubectl delete -f httproute.gke.yaml -n ${NAMESPACE}
```

#### Cleanup for "digitalocean"

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
