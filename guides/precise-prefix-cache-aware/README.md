# Feature: Precise Prefix Cache Aware Routing

## Overview

This guide demonstrates how to configure the inference scheduler to use the new precise prefix cache aware routing based on [vLLM KV-Events](https://github.com/vllm-project/vllm/issues/16669) data. Precise prefix cache aware routing pulls up-to-date prefix cache status from serving instances, eliminating the need for additional indexing services and increasing cache hit rate at high throughput.

## Hardware Requirements

This example out of the box uses 16 GPUs (8 replicas x 2 GPUs each) of any supported kind:

- **NVIDIA GPUs**: Any NVIDIA GPU (support determined by the inferencing image used)
- **Intel XPU/GPUs**: Intel Data Center GPU Max 1550 or compatible Intel XPU device

**Using fewer accelerators**: Fewer accelerators can be used by modifying the `values.yaml` corresponding to your deployment. For example, to use only 2 GPUs with the default NVIDIA GPU deployment, update `replicas: 2` in [ms-kv-events/values.yaml](./ms-kv-events/values.yaml#L16-L21).


## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
- Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-precise # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-precise` in this example.

### Deploy

```bash
cd guides/precise-prefix-cache-aware
helmfile apply -n ${NAMESPACE}
```

**_Experimental_**: Disaggregated Tokenization
In this path, the precise-prefix-cache-scorer plugin tokenizes and processes the user input in order to eventually compute prefix-cache hits.
By default, the logic for tokenization and preprocessing is embedded within the inference scheduler.

Through this experimental feature, the inference scheduler can delegate the preprocessing and tokenization of inputs to a tokenization-service deployed as a sidecar.
To use, run this command instead of the above:

```bash
cd guides/precise-prefix-cache-aware
DISAGGREGATED_TOKENIZATION=true helmfile apply -n ${NAMESPACE}
```

**_Experimental_**: Pod Discovery Mode
By default, the KV events are published to a centralized ZMQ endpoint on the inference scheduler. With pod discovery mode, each vLLM pod publishes KV events on its own endpoint (`tcp://*:5557`), and the inference scheduler discovers and connects to these endpoints automatically.
This is useful for active-active multi-scheduler deployments - to maintain a global view in each replica.

To enable pod discovery mode:

```bash
cd guides/precise-prefix-cache-aware
POD_DISCOVERY=true helmfile apply -n ${NAMESPACE}
```

**_NOTE:_** Pod discovery mode and disaggregated tokenization are mutually exclusive options.

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=kv-events-2 helmfile apply -n ${NAMESPACE}`

**_NOTE:_** This uses Istio as the default provider, see [Gateway Options](./README.md#gateway-options) for installing with a specific provider.

### Gateway options

To see specify your gateway choice you can use the `-e <gateway option>` flag, ex:

```bash
helmfile apply -e kgateway -n ${NAMESPACE}
```

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../docs/customizing-your-gateway.md).

#### Intel XPU deployment

```bash
helmfile apply -e xpu -n ${NAMESPACE} # targets istio as gateway provider with Intel XPU hardware
```

You can also combine Intel XPU hardware with different gateway providers:

```bash
helmfile apply -e xpu-kgateway -n ${NAMESPACE} # targets kgateway as gateway provider with Intel XPU hardware
```

With pod discovery mode:

```bash
POD_DISCOVERY=true helmfile apply -e xpu -n ${NAMESPACE}
```

### Install HTTPRoute

Follow provider specific instructions for installing HTTPRoute.

#### Install for "kgateway" or "istio"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

#### Install for "gke"

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

## Verify the Installation

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                           APP VERSION
gaie-kv-events  llm-d-precise  1               2026-01-28 18:16:14.302723 +0200 IST    deployed        inferencepool-v1.3.0            v1.3.0
infra-kv-events llm-d-precise  1               2026-01-28 18:16:08.733157 +0200 IST    deployed        llm-d-infra-v1.3.6              v0.3.0
ms-kv-events    llm-d-precise  1               2026-01-28 18:16:26.907329 +0200 IST    deployed        llm-d-modelservice-v0.4.5       v0.4.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                          READY   STATUS    RESTARTS   AGE
pod/gaie-kv-events-epp-9c9849bf6-ftcfb                        1/1     Running     0          16h
pod/infra-kv-events-inference-gateway-istio-df9977d89-5zp6z   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-dqv8d   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-fcbmf   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-frpk8   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-g72ls   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-kf8r8   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-kqhd2   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-t8srp   1/1     Running     0          16h
pod/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-vnnnv   1/1     Running     0          16h

NAME                                              TYPE           CLUSTER-IP   EXTERNAL-IP   PORT(S)                        AGE
service/gaie-kv-events-epp                        ClusterIP   172.30.193.29    <none>        9002/TCP,9090/TCP,5600/TCP   16h
service/gaie-kv-events-ip-805c964d                ClusterIP   None             <none>        54321/TCP                    16h
service/infra-kv-events-inference-gateway-istio   ClusterIP   172.30.18.110    <none>        15021/TCP,80/TCP             16h

NAME                                                      READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-kv-events-epp                        1/1     1            1           16h
deployment.apps/infra-kv-events-inference-gateway-istio   1/1     1            1           16h
deployment.apps/ms-kv-events-llm-d-modelservice-decode    8/8     8            8           16h

NAME                                                                DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-kv-events-epp-9c9849bf6                        1         1         1       16h
replicaset.apps/infra-kv-events-inference-gateway-istio-df9977d89   1         1         1       16h
replicaset.apps/ms-kv-events-llm-d-modelservice-decode-548bfbc7d6   8         8         8       16h
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Testing this "well lit path"

We have docs on getting started sending inference requests [available here](../../docs/getting-started-inferencing.md) that are general to all examples. However, this example has unique instructions to interact with it which will be provided here:

1. First, you will need to send a basic inference request to your gateway. For in depth documentation on how to do this, please see the link above, but a command will be provided to work out of the box with default settings:

```bash
kubectl port-forward -n ${NAMESPACE} service/infra-kv-events-inference-gateway-istio 8000:80
export LONG_TEXT_200_WORDS="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."

curl -s http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-32B",
    "prompt": "'"$LONG_TEXT_200_WORDS"'",
    "max_tokens": 50
  }' | jq
```

1. Check the inference-scheduler's prefix-cache-scorer's scores with the following command:

```bash
kubectl logs -l inferencepool=gaie-kv-events-epp -n ${NAMESPACE} --tail 100 | grep "Calculated score" | grep "precise-prefix-cache-scorer/precise-prefix-cache-scorer"
```

You should see output similar to:

```json
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-g72ls-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-kf8r8-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-kqhd2-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-dqv8d-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-t8srp-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-frpk8-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-fcbmf-rank-0","namespace":"dpikus-precise"},"score":0}
{"level":"Level(-4)","ts":"2026-01-29T08:59:51Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"b2b768f3-ad23-4867-9505-a69caacc01d3","objectiveKey":"","incomingModelName":"Qwen/Qwen3-32B","targetModelName":"Qwen/Qwen3-32B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-548bfbc7d6-vnnnv-rank-0","namespace":"dpikus-precise"},"score":0}
```

1. Repeat the steps above to see the prefix-cache-scorer in action

You should see output similar to:

```json
{"level":"Level(-4)","ts":"2025-10-07T16:09:21Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"f4c967aa-ad15-4be2-8640-55164da18dfa","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-pbp84","namespace":"llm-d-precise"},"score":0}
{"level":"Level(-4)","ts":"2025-10-07T16:09:21Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"f4c967aa-ad15-4be2-8640-55164da18dfa","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-kgnqh","namespace":"llm-d-precise"},"score":1}
```

**_NOTE:_** These logs will only appear for unique requests, so if you don't see repeated instances of these logs make sure to redo them in a unique way.

Notice that the second time we called the `/v1/completions` endpoint, the prefix-cache-scorer was able to return a score for the pod,
indicating that it had cached the KV-blocks from the first call.

## Benchmarking

To run benchmarks against the installed llm-d stack, you need [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh), a template file from [guides/benchmark](../benchmark/), and a Persistent Volume Claim (PVC) to store the results. Follow the instructions in the [benchmark doc](../benchmark/README.md).

### Example

This example uses [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh) with the template [precise_template.yaml](../benchmark/precise_template.yaml).

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a shared prefix synthetic workload named `shared_prefix_synthetic`. This workload runs several stages with different rates. The results will be stored on the provided PVC, accessible through the `llmdbench-harness-launcher` pod. Each experiment is saved under the `requests` folder, e.g.,/`requests/inference-perf_<experiment ID>_shared_prefix_precise-guide-<model name>` folder.

Several results files will be created (see [Benchmark doc](../benchmark/README.md)), including a yaml file in a "standard" benchmark report format (see [Benchmark Report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md)).

The `bash` commands below downloads the benchmark runner script (`run_only.sh`), then presents an interactive menu of Precise-Prefix benchmark templates from the llm-d repository's [`guides/benchmark/`](../benchmark/) directory. Once the user selects a template, it downloads that specific YAML configuration file for running benchmarks.

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  select f in $(
      curl -s https://api.github.com/repos/llm-d/llm-d/contents/guides/benchmark?ref=main |
      sed -n '/[[:space:]]*"name":[[:space:]][[:space:]]*"\(precise.*\_template\.yaml\)".*/ s//\1/p'
    ); do
    curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/benchmark/$f"
    break
  done
  ```

Choose the `precise_template.yaml` template, then run:

  ```bash
  export NAMESPACE=llm-d-precise     # replace with your namespace
  export BENCHMARK_PVC=workload-pvc   # replace with your PVC name
  export GATEWAY_SVC=infra-kv-events-inference-gateway-istio  # replace with your exact service name
  envsubst < precise_template.yaml > config.yaml
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
  2026-01-28 18:06:20,130 - inference_perf.client.filestorage.local - INFO - Report files will be stored at: /requests/inference-perf_1769623549_shared_prefix-precise-guide-Qwen3-32B
  2026-01-28 18:06:23,584 - inference_perf.loadgen.load_generator - INFO - Stage 0 - run started
  Stage 0 progress: 100%|█████████▉| 0.996/1.0 [01:19<00:00, 35.86s/it]             2026-01-28 18:07:43,989 - inference_perf.loadgen.load_generator - INFO - Stage 0 - run completed
  Stage 0 progress: 100%|██████████| 1.0/1.0 [01:20<00:00, 80.08s/it]
  2026-01-28 18:07:44,990 - inference_perf.loadgen.load_generator - INFO - Stage 1 - run started
  Stage 1 progress: 100%|██████████| 1.0/1.0 [00:38<00:00, 38.04s/it]
  2026-01-28 18:08:23,032 - inference_perf.loadgen.load_generator - INFO - Stage 1 - run completed
  Stage 2 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-28 18:08:24,033 - inference_perf.loadgen.load_generator - INFO - Stage 2 - run started
  Stage 2 progress:  98%|█████████▊| 0.98/1.0 [00:42<00:00, 17.81s/it]2026-01-28 18:09:07,078 - inference_perf.loadgen.load_generator - INFO - Stage 2 - run completed
  Stage 2 progress: 100%|██████████| 1.0/1.0 [00:43<00:00, 43.04s/it]
  2026-01-28 18:09:08,079 - inference_perf.loadgen.load_generator - INFO - Stage 3 - run started
  Stage 3 progress: 100%|██████████| 1.0/1.0 [00:43<00:00, 43.05s/it]
  2026-01-28 18:09:51,133 - inference_perf.loadgen.load_generator - INFO - Stage 3 - run completed
  Stage 4 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-28 18:09:52,134 - inference_perf.loadgen.load_generator - INFO - Stage 4 - run started
  Stage 4 progress: 100%|██████████| 1.0/1.0 [01:12<00:00, 72.07s/it]
  2026-01-28 18:11:04,214 - inference_perf.loadgen.load_generator - INFO - Stage 4 - run completed
  2026-01-28 18:11:05,215 - inference_perf.loadgen.load_generator - INFO - Stage 5 - run started
  Stage 5 progress: 100%|██████████| 1.0/1.0 [01:07<00:00, 67.08s/it]
  2026-01-28 18:12:12,296 - inference_perf.loadgen.load_generator - INFO - Stage 5 - run completed
  Stage 6 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-28 18:12:13,297 - inference_perf.loadgen.load_generator - INFO - Stage 6 - run started
  Stage 6 progress:  99%|█████████▊| 0.9866666666666667/1.0 [01:04<00:00, 28.03s/it]2026-01-28 18:13:18,367 - inference_perf.loadgen.load_generator - INFO - Stage 6 - run completed
  Stage 6 progress: 100%|██████████| 1.0/1.0 [01:05<00:00, 65.06s/it]
  2026-01-28 18:13:19,367 - inference_perf.loadgen.load_generator - INFO - Stage 7 - run started
  Stage 7 progress:  99%|█████████▊| 0.9866666666666667/1.0 [01:01<00:00, 26.38s/it]2026-01-28 18:14:21,444 - inference_perf.loadgen.load_generator - INFO - Stage 7 - run completed
  Stage 7 progress: 100%|██████████| 1.0/1.0 [01:02<00:00, 62.07s/it]
  Stage 8 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-28 18:14:22,445 - inference_perf.loadgen.load_generator - INFO - Stage 8 - run started
  Stage 8 progress: 100%|██████████| 1.0/1.0 [00:59<00:00, 59.07s/it]
  2026-01-28 18:15:21,531 - inference_perf.loadgen.load_generator - INFO - Stage 8 - run completed
  2026-01-28 18:15:22,531 - inference_perf.loadgen.load_generator - INFO - Stage 9 - run started
  Stage 9 progress: 100%|██████████| 1.0/1.0 [01:49<00:00, 109.11s/it]
  2026-01-28 18:17:11,663 - inference_perf.loadgen.load_generator - INFO - Stage 9 - run completed
  2026-01-28 18:17:12,665 - inference_perf.loadgen.load_generator - INFO - Stage 10 - run started
  Stage 10 progress: 100%|█████████▉| 0.9974160206718347/1.0 [01:54<00:00, 230.22s/it]2026-01-28 18:19:07,802 - inference_perf.loadgen.load_generator - INFO - Stage 10 - run completed
  Stage 10 progress: 100%|██████████| 1.0/1.0 [01:55<00:00, 115.12s/it]
  Stage 11 progress:   0%|          | 0/1.0 [00:00<?, ?it/s]2026-01-28 18:19:08,803 - inference_perf.loadgen.load_generator - INFO - Stage 11 - run started
  Stage 11 progress: 100%|█████████▉| 0.9980237154150198/1.0 [01:50<00:00, 131.50s/it]2026-01-28 18:20:59,920 - inference_perf.loadgen.load_generator - INFO - Stage 11 - run completed
  Stage 11 progress: 100%|██████████| 1.0/1.0 [01:51<00:00, 111.10s/it]
  2026-01-28 18:21:00,921 - inference_perf.loadgen.load_generator - INFO - Stage 12 - run started
  Stage 12 progress: 100%|█████████▉| 0.998639455782313/1.0 [01:46<00:00, 120.81s/it] 2026-01-28 18:22:48,084 - inference_perf.loadgen.load_generator - INFO - Stage 12 - run completed
  Stage 12 progress: 100%|██████████| 1.0/1.0 [01:47<00:00, 107.13s/it]
  2026-01-28 18:22:49,085 - inference_perf.loadgen.load_generator - INFO - Stage 13 - run started
  Stage 13 progress: 100%|██████████| 1.0/1.0 [01:49<00:00, 157.90s/it]               2026-01-28 18:24:38,230 - inference_perf.loadgen.load_generator - INFO - Stage 13 - run completed
  Stage 13 progress: 100%|██████████| 1.0/1.0 [01:49<00:00, 109.13s/it]
  2026-01-28 18:24:39,231 - inference_perf.loadgen.load_generator - INFO - Stage 14 - run started
  Stage 14 progress: 100%|█████████▉| 0.997979797979798/1.0 [01:45<00:00, 103.83s/it] 2026-01-28 18:26:26,763 - inference_perf.loadgen.load_generator - INFO - Stage 14 - run completed
  Stage 14 progress: 100%|██████████| 1.0/1.0 [01:47<00:00, 107.13s/it]
  2026-01-28 18:26:27,764 - inference_perf.loadgen.load_generator - INFO - Stage 15 - run started
  Stage 15 progress: 100%|██████████| 1.0/1.0 [01:48<00:00, 108.14s/it]
  2026-01-28 18:28:15,925 - inference_perf.loadgen.load_generator - INFO - Stage 15 - run completed
  2026-01-28 18:28:16,926 - inference_perf.loadgen.load_generator - INFO - Stage 16 - run started
  Stage 16 progress: 100%|█████████▉| 0.9973333333333333/1.0 [01:49<00:00, 219.58s/it]2026-01-28 18:30:07,091 - inference_perf.loadgen.load_generator - INFO - Stage 16 - run completed
  Stage 16 progress: 100%|██████████| 1.0/1.0 [01:50<00:00, 110.15s/it]
  2026-01-28 18:30:08,098 - inference_perf.reportgen.base - INFO - Generating Reports...
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
        max: 0.18811781704425812
        mean: 0.020669583557040024
        min: 5.517038516700268e-06
        p0p1: 2.6719751942437142e-05
        p1: 0.014419194809161127
        p10: 0.01656536371447146
        p25: 0.018567895487649366
        p5: 0.015728384861722587
        p50: 0.020695073500974104
        p75: 0.021503399751964025
        p90: 0.02207457079202868
        p95: 0.022546104292268866
        p99: 0.08946080405090495
        p99p9: 0.10259400638758956
        units: s/token
      normalized_time_per_output_token:
        max: 0.717500958187884
        mean: 0.04021876003301928
        min: 0.01744550473873396
        p0p1: 0.01751962216022992
        p1: 0.0180509185810905
        p10: 0.01923465710929764
        p25: 0.02040626925639579
        p5: 0.018997717847623295
        p50: 0.021183353236022558
        p75: 0.021865379507869025
        p90: 0.022936470821125987
        p95: 0.07059941448146335
        p99: 0.6672186557748682
        p99p9: 0.7082893342302221
        units: s/token
      request_latency:
        max: 22.989790733961854
        mean: 20.847309030755714
        min: 17.35827721504029
        p0p1: 17.44975291375129
        p1: 17.961483361392748
        p10: 19.066985952935646
        p25: 20.198816913514747
        p5: 18.737510592667967
        p50: 20.977808750525583
        p75: 21.562361991251237
        p90: 22.255666058021596
        p95: 22.56433772156015
        p99: 22.960098422522424
        p99p9: 22.98521691379562
        units: s
      time_per_output_token:
        max: 0.022786254265985916
        mean: 0.020669583557040027
        min: 0.017162352968996857
        p0p1: 0.017250305588040735
        p1: 0.01777372067991237
        p10: 0.018948828270088418
        p25: 0.020017497138731414
        p5: 0.01861606290267373
        p50: 0.020810300373967038
        p75: 0.021364415241492678
        p90: 0.022014989444997628
        p95: 0.022341811860160668
        p99: 0.02270649872458249
        p99p9: 0.02278452591815719
        units: s/token
      time_to_first_token:
        max: 0.37973733502440155
        mean: 0.1458328293156228
        min: 0.05756738397758454
        p0p1: 0.057710750947357156
        p1: 0.058549812618875874
        p10: 0.07117205987451598
        p25: 0.07867017023090739
        p5: 0.06478812359273434
        p50: 0.15742571302689612
        p75: 0.17041571525624022
        p90: 0.21869741418631744
        p95: 0.24442302147799633
        p99: 0.3229195393121335
        p99p9: 0.369758901104099
        units: s
    requests:
      failures: 0
      input_length:
        max: 7678.0
        mean: 7578.415
        min: 7510.0
        p0p1: 7512.388
        p1: 7523.98
        p10: 7543.9
        p25: 7557.0
        p5: 7535.9
        p50: 7575.0
        p75: 7596.25
        p90: 7618.0
        p95: 7629.15
        p99: 7653.04
        p99p9: 7673.821
        units: count
      output_length:
        max: 1001.0
        mean: 938.355
        min: 32.0
        p0p1: 32.0
        p1: 32.99
        p10: 955.6
        p25: 990.75
        p5: 316.00000000000006
        p50: 997.0
        p75: 1000.0
        p90: 1000.0
        p95: 1000.0
        p99: 1000.0
        p99p9: 1000.801
        units: count
      total: 200
    throughput:
      output_tokens_per_sec: 4511.146742060757
      requests_per_sec: 4.807505413261246
      total_tokens_per_sec: 40944.41787850098
    time:
      duration: 21.37610672903247
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
          base_url: http://infra-kv-events-inference-gateway-istio.dpikus-precise.svc.cluster.local:80
          ignore_eos: true
          model_name: Qwen/Qwen3-32B
          type: vllm
        storage:
          google_cloud_storage: null
          local_storage:
            path: /requests/inference-perf_1769623549_shared_prefix_precise-guide-Qwen3-32B
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

- **Throughput**: Requests/sec **108.7%** ; Output tokens/sec **109.4%**
- **Latency**: TTFT **-99.7%** ; E2E request latency **-42.8%**
- **Per-token speed**: Time per output token **13.0%** (**slower**)

| Metric (median)                                                   | k8s         | llmd        | Δ (llmd - k8s) | Δ% vs k8s |
|:------------------------------------------------------------------|:------------|:------------|:---------------|:----------|
| Requests/sec                                                      | 5.7542      | 12.0101     | 6.2559         | 108.7%    |
| Input tokens/sec                                                  | 43,609.4980 | 91,001.8874 | 47,392.3894    | 108.7%    |
| Output tokens/sec                                                 | 5,390.6289  | 11,290.1722 | 5,899.5433     | 109.4%    |
| Total tokens/sec                                                  | 49,000.1269 | 102,292.0596| 53,291.9326    | 108.8%    |
| Approx. gen speed (1 / time_per_output_token) [tok/s/request]     | 21.614      | 19.121      | -2.493         | -11.5%    |
| Request latency (s)                                               | 93.156      | 53.263      | -39.893        | -42.8%    |
| TTFT (s)                                                          | 47.676      | 0.156       | -47.520        | -99.7%    |
| Time/output token (ms)                                            | 46.27       | 52.30       | 6.03           | 13.0%     |
| Inter-token latency (ms)                                          | 30.57       | 41.46       | 10.89          | 35.6%     |

## Cleanup

To remove the deployment:

```bash
# Remove the model services
# From examples/precise-prefix-cache-aware
helmfile destroy -n ${NAMESPACE}

# Or uninstall manually
helm uninstall infra-kv-events -n ${NAMESPACE}
helm uninstall gaie-kv-events -n ${NAMESPACE}
helm uninstall ms-kv-events -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
