# How to run a benchmark workload against your LLM-d stack

## Overview

This document describes how to run benchmarks against a deployed llm-d stack.
For full, customizable benchmarking, please refer to [llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark). `llm-d-benchmark` includes advanced features, such as automatic stack creation, sweeping of configuration parameters, recommendations, etc.

## Requirements

- You are assumed to have deployed the llm-d inference stack from a guide, or otherwise followed the llm-d conventions for deployment.
- Install `yq` (YAML processor) - version>=4 (see [Client Setup](../prereq/client-setup/README.md))
- For MacOS users: if `timeout` utility is not present, install it with `brew install coreutils` command.
- Download the benchmark script [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh) and make it executable.

    ```bash
    curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
    chmod u+x run_only.sh
    ```

- Prepare a Persistent Volume Claim (PVC) to store the benchmark results. The PVC must have `RWX` write permissions and be large enough (`200Gi` recommended).

  <details>
  <summary><b><i>Click</i></b> here if you need to create a new PVC</summary>

    ```yaml
    BENCHMARK_PVC="<name of new PVC>"   # e.g., "workload-pvc"
    cat <<YAML | kubectl -n ${NAMESPACE} apply -f -
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata:
      name: ${BENCHMARK_PVC}  # choose your PVC name
    spec:
      accessModes:
        - ReadWriteMany
      resources:
        requests:
          storage: 200Gi
      # storageClassName: <change the default storage class if needed>
    YAML
    ```

    Alternatively, a PVC can be created via the UI of the cluster dashboard.

  </details>

## Set your namespace, PVC, and project root directory

  ```bash
  export NAMESPACE="<your namespace>"
  export BENCHMARK_PVC="<name of your PVC>"
  export LLMD_ROOT_DIR=../..   # where you cloned llm-d/llm-d
  export BENCH_TEMPLATE_DIR="${LLMD_ROOT_DIR}"/guides/benchmark
  ```

## Set your stack type and gateway name

`GATEWAY_SVC` is your gateway service name.
`BENCHMARK_TEMPLATE` is a corresponding benchmark template file (available in [guides/benchmark](./)).

> [!IMPORTANT]
> Choose the option that matches your stack type:
> <table>
> <tr>
> <td>
> <details>
> <summary><b>Intelligent Inference Scheduling</b></summary>
>
> ```bash
> export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" \
>   -l gateway.networking.k8s.io/gateway-name=infra-inference-scheduling-inference-gateway \
>   --no-headers  -o=custom-columns=:metadata.name \
>   | head -1
> )
> export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/inference_scheduling_template.yaml
> # export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/inference_scheduling_guidellm_template.yaml
> # export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/inference_scheduling_shared_prefix_template.yaml
>   ```
>
> </details>
> </td>
>
> <td>
> <details>
> <summary><b>Prefill/Decode Disaggregation</b></summary>
>
> ```bash
> export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" \
>   -l gateway.networking.k8s.io/gateway-name=infra-pd-inference-gateway \
>   --no-headers  -o=custom-columns=:metadata.name \
>   | head -1
> )
> export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/pd_template.yaml
> #export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/pd_vllm_bench_random_concurrent_template.yaml
> #export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/pd_shared_prefix_template.yaml
>   ```
>
> </details>
> </td>
> </tr>
>
> <tr>
> <td>
> <details>
> <summary><b>Wide Expert-Parallelism</b></summary>
>
> ```bash
> export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" \
>   -l gateway.networking.k8s.io/infra-wide-wp-inference-gateway \
>   --no-headers  -o=custom-columns=:metadata.name \
>   | head -1
> )
> export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/wide_ep_template.yaml
>   ```
>
> </details>
> </td>
> <td>
> <details>
> <summary><b>Tiered Prefix Cache</b></summary>
>
> TBD
>
> </details>
> </tr>
> <tr>
> <td>
> <details>
> <summary><b>Precise Prefix Caching</b></summary>
>
> ```bash
> export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" \
>   -l gateway.networking.k8s.io/gateway-name=infra-kv-events-inference-gateway \
>   --no-headers  -o=custom-columns=:metadata.name \
>   | head -1
> )
> export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/precise_template.yaml
> # export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/precise_guidellm_template.yaml
> # export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/precise_shared_prefix_template.yaml
>   ```
>
> </details>
> </td>
> </tr>
> </table>

Check your env:

  ```bash
  echo "Using NAMESPACE=${NAMESPACE:?Missing}, GATEWAY_SVC=${GATEWAY_SVC:?Missing}, BENCHMARK_PVC=${BENCHMARK_PVC:?Missing}, BENCHMARK_TEMPLATE=${BENCHMARK_TEMPLATE:?Missing}"
  ```

## Run

Create a yaml configuration file for the benchmark and run.

  ```bash
  envsubst < ${BENCHMARK_TEMPLATE} > config.yaml
  ./run_only.sh -c config.yaml
  ```

The benchmarks will create a launcher pod to run and the resulted would be stored on the PVC.
You can try running with different workload configuration. Just edit the `workload` section in `config.yaml` and rerun (for details, see [Advanced.workload](README.md#workload) below).

## Analyze Results

You can access the results PVC through the benchmark launcher pod.

  ```bash
  export HARNESS_POD=$(kubectl get pods -n ${NAMESPACE} -l app --show-labels | awk -v p='lmdbench-.*-launcher' '$0~p {print $1; exit}')
  kubectl exec $HARNESS_POD -n $NAMESPACE -- ls /requests
  ```

To copy a results directory to your local machine use:

  ```bash
  kubectl cp ${NAMESPACE}/${HARNESS_POD}:/requests/<results-folder> <destination-path>
  ```

## Results Examples

### Terminal output

`run_only.sh` prints progress messages to the terminal. The stdout and stderr of the harness itself is printed to the terminal as well as captured in the results.

This example uses `guidellm` with a [`rate_comparison`](./inference_scheduling_guidellm_template.yaml) workload:

  ```bash
  export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/inference_scheduling_guidellm_template.yaml
  ```

<details>

<summary><b><i>Click</i></b> to view the terminal output of <code>run_only.sh</code> using <code>guidellm</code></summary>

  ```bash
  ✦ ❯  ./run_only.sh -c config.yaml

  ===> Mon Dec 29 18:14:20 IST 2025 - ./run_only.sh:63
  📄 Reading configuration file config.yaml
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:20 IST 2025 - ./run_only.sh:63
  ℹ️ Using endpoint_stack_name=inference-scheduling-Qwen3-32B on endpoint_namespace=dean-ns1 running model=Qwen/Qwen3-32B at endpoint_base_url=http://infra-inference-scheduling-inference-gateway-istio.dean-ns1.svc.cluster.local:80
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:20 IST 2025 - ./run_only.sh:63
  ℹ️ Using harness_name=guidellm, with _harness_pod_name=llmdbench-harness-launcher on harness_namespace=dean-ns1
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:20 IST 2025 - ./run_only.sh:63
  🔧 Ensuring harness namespace is prepared
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:20 IST 2025 - ./run_only.sh:63
  🔧 Verifying HF token secret llm-d-hf-token in namespace dean-ns1
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:21 IST 2025 - ./run_only.sh:63
  ℹ️ Using HF token secret llm-d-hf-token
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:21 IST 2025 - ./run_only.sh:63
  🔍 Verifying model Qwen/Qwen3-32B on endpoint http://infra-inference-scheduling-inference-gateway-istio.dean-ns1.svc.cluster.local:80/v1/completions using pod verify-model-1767024860
  ------------------------------------------------------------
  HTTP/1.1 200 OK
  x-envoy-upstream-service-time: 41
  x-went-into-resp-headers: true
  content-type: application/json
  date: Mon, 29 Dec 2025 16:14:23 GMT
  server: istio-envoy
  transfer-encoding: chunked

  {"choices":[{"finish_reason":"length","index":0,"logprobs":null,"prompt_logprobs":null,"prompt_token_ids":null,"stop_reason":null,"text":"Question::HelloQuestion() {\n    question = \"Hello, World!\";\n    answer","token_ids":null}],"created":1767024864,"id":"cmpl-36726d69-84e3-48c6-98c1-8161ca9dce8a","kv_transfer_params":null,"model":"Qwen/Qwen3-32B","object":"text_completion","service_tier":null,"system_fingerprint":null,"usage":{"completion_tokens":16,"prompt_tokens":1,"prompt_tokens_details":null,"total_tokens":17}}
  ===> Mon Dec 29 18:14:26 IST 2025 - ./run_only.sh:63
  🔧 Preparing ConfigMap with workload profiles
  ------------------------------------------------------------
  configmap "guidellm-profiles" deleted from dean-ns1 namespace
  configmap/guidellm-profiles created

  ===> Mon Dec 29 18:14:28 IST 2025 - ./run_only.sh:63
  ℹ️ ConfigMap 'guidellm-profiles' created
  ------------------------------------------------------------

  ===> Mon Dec 29 18:14:28 IST 2025 - ./run_only.sh:63
  ℹ️ Checking results PVC
  ------------------------------------------------------------
  Name:          workload-pvc
  Namespace:     dean-ns1
  StorageClass:  ibm-spectrum-scale-fileset
  Status:        Bound
  Volume:        pvc-1c915c2c-5b37-43bb-b43f-9c8b1344528e
  Labels:        <none>
  Annotations:   pv.kubernetes.io/bind-completed: yes
                pv.kubernetes.io/bound-by-controller: yes
                volume.beta.kubernetes.io/storage-provisioner: spectrumscale.csi.ibm.com
                volume.kubernetes.io/storage-provisioner: spectrumscale.csi.ibm.com
  Finalizers:    [kubernetes.io/pvc-protection]
  Capacity:      200Gi
  Access Modes:  RWX
  VolumeMode:    Filesystem
  Used By:       <none>
  Events:        <none>

  ===> Mon Dec 29 18:14:30 IST 2025 - ./run_only.sh:63
  ℹ️ Creating harness pod llmdbench-harness-launcher
  ------------------------------------------------------------
  Warning: would violate PodSecurity "restricted:latest": allowPrivilegeEscalation != false (container "harness" must set securityContext.allowPrivilegeEscalation=false), unrestricted capabilities (container "harness" must set securityContext.capabilities.drop=["ALL"]), runAsNonRoot != true (pod or container "harness" must set securityContext.runAsNonRoot=true), runAsUser=0 (container "harness" must not set runAsUser=0), seccompProfile (pod or container "harness" must set securityContext.seccompProfile.type to "RuntimeDefault" or "Localhost")
  pod/llmdbench-harness-launcher created
  pod/llmdbench-harness-launcher condition met

  ===> Mon Dec 29 18:14:35 IST 2025 - ./run_only.sh:63
  ℹ️ Harness pod llmdbench-harness-launcher started
  ------------------------------------------------------------
  Name:             llmdbench-harness-launcher
  Namespace:        dean-ns1
  Priority:         0
  Service Account:  default
  Node:             pokprod-b93r43s0/192.168.98.39
  Start Time:       Mon, 29 Dec 2025 18:14:32 +0200
  Labels:           app=llmdbench-harness-launcher
  Annotations:      k8s.ovn.org/pod-networks:
                      {"default":{"ip_addresses":["10.130.7.105/23"],"mac_address":"0a:58:0a:82:07:69","gateway_ips":["10.130.6.1"],"routes":[{"dest":"10.128.0....
                    k8s.v1.cni.cncf.io/network-status:
                      [{
                          "name": "ovn-kubernetes",
                          "interface": "eth0",
                          "ips": [
                              "10.130.7.105"
                          ],
                          "mac": "0a:58:0a:82:07:69",
                          "default": true,
                          "dns": {}
                      }]
                    openshift.io/scc: anyuid
  Status:           Running
  IP:               10.130.7.105
  IPs:
    IP:  10.130.7.105
  Containers:
    harness:
      Container ID:  cri-o://2d450837cc20f303e9635b70897a40865bf4b44f1024e50c41d3c858b21f1db7
      Image:         ghcr.io/llm-d/llm-d-benchmark:v0.4.0
      Image ID:      ghcr.io/llm-d/llm-d-benchmark@sha256:585d61309bcfa02ee4b02bc0bc45b72d410b975be0a72e9c8f597eb0326815be
      Port:          <none>
      Host Port:     <none>
      Command:
        sh
        -c
      Args:
        sleep 1000000
      State:          Running
        Started:      Mon, 29 Dec 2025 18:14:34 +0200
      Ready:          True
      Restart Count:  0
      Limits:
        cpu:     16
        memory:  32Gi
      Requests:
        cpu:     16
        memory:  32Gi
      Environment:
        LLMDBENCH_RUN_WORKSPACE_DIR:                  /workspace
        LLMDBENCH_MAGIC_ENVAR:                        harness_pod
        LLMDBENCH_HARNESS_NAME:                       guidellm
        LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX:  /requests
        LLMDBENCH_RUN_DATASET_DIR:                    /workspace
        LLMDBENCH_HARNESS_STACK_NAME:                 inference-scheduling-Qwen3-32B
      Mounts:
        /requests from results (rw)
        /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-4jjd2 (ro)
        /workspace/profiles/guidellm from guidellm-profiles (rw)
  Conditions:
    Type                        Status
    PodReadyToStartContainers   True
    Initialized                 True
    Ready                       True
    ContainersReady             True
    PodScheduled                True
  Volumes:
    results:
      Type:       PersistentVolumeClaim (a reference to a PersistentVolumeClaim in the same namespace)
      ClaimName:  workload-pvc
      ReadOnly:   false
    guidellm-profiles:
      Type:      ConfigMap (a volume populated by a ConfigMap)
      Name:      guidellm-profiles
      Optional:  false
    kube-api-access-4jjd2:
      Type:                    Projected (a volume that contains injected data from multiple sources)
      TokenExpirationSeconds:  3607
      ConfigMapName:           kube-root-ca.crt
      Optional:                false
      DownwardAPI:             true
      ConfigMapName:           openshift-service-ca.crt
      Optional:                false
  QoS Class:                   Guaranteed
  Node-Selectors:              <none>
  Tolerations:                 node.kubernetes.io/memory-pressure:NoSchedule op=Exists
                              node.kubernetes.io/not-ready:NoExecute op=Exists for 300s
                              node.kubernetes.io/unreachable:NoExecute op=Exists for 300s
  Events:
    Type    Reason                  Age   From                     Message
    ----    ------                  ----  ----                     -------
    Normal  Scheduled               3s    default-scheduler        Successfully assigned dean-ns1/llmdbench-harness-launcher to pokprod-b93r43s0
    Normal  SuccessfulAttachVolume  3s    attachdetach-controller  AttachVolume.Attach succeeded for volume "pvc-1c986c2c-5c37-44bb-b43f-9c871904428e"
    Normal  AddedInterface          2s    multus                   Add eth0 [10.130.7.105/23] from ovn-kubernetes
    Normal  Pulling                 2s    kubelet                  Pulling image "ghcr.io/llm-d/llm-d-benchmark:v0.4.0"
    Normal  Pulled                  2s    kubelet                  Successfully pulled image "ghcr.io/llm-d/llm-d-benchmark:v0.4.0" in 285ms (285ms including waiting). Image size: 3174696470 bytes.
    Normal  Created                 2s    kubelet                  Created container: harness
    Normal  Started                 2s    kubelet                  Started container harness

  ===> Mon Dec 29 18:14:36 IST 2025 - ./run_only.sh:63
  ℹ️ Running benchmark with workload rate_comparison
  ------------------------------------------------------------
  LLMDBENCH_CONTROL_WORK_DIR=/requests/guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B
  LLMDBENCH_HARNESS_GIT_BRANCH=adfa108ab1df6f2a1452d1037a71817a493303a8
  LLMDBENCH_HARNESS_GIT_REPO=https://github.com/vllm-project/guidellm.git
  LLMDBENCH_HARNESS_NAME=guidellm
  LLMDBENCH_HARNESS_STACK_NAME=inference-scheduling-Qwen3-32B
  LLMDBENCH_MAGIC_ENVAR=harness_pod
  LLMDBENCH_RUN_DATASET_DIR=/workspace
  LLMDBENCH_RUN_EXPERIMENT_ANALYZER=guidellm-analyze_results.sh
  LLMDBENCH_RUN_EXPERIMENT_HARNESS=guidellm-llm-d-benchmark.sh
  LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=guidellm
  LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=1
  LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=0
  LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=0
  LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=rate_comparison.yaml
  LLMDBENCH_RUN_EXPERIMENT_ID=1767024860_rate_comparison
  LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B
  LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX=/requests
  LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX=guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B
  LLMDBENCH_RUN_WORKSPACE_DIR=/workspace
  Running harness: /usr/local/bin/guidellm-llm-d-benchmark.sh
  Using experiment result dir: /requests/guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B
  ✔ OpenAIHTTPBackend backend validated with model Qwen/Qwen3-32B
    {'target':
    'http://infra-inference-scheduling-inference-gateway-istio.dean-ns1.svc.cluste
    r.local:80', 'model': 'Qwen/Qwen3-32B', 'timeout': 60.0, 'http2': True,
    'follow_redirects': True, 'verify': False, 'openai_paths': {'health':
    'health', 'models': 'v1/models', 'text_completions': 'v1/completions',
    'chat_completions': 'v1/chat/completions', 'audio_transcriptions':
    'v1/audio/transcriptions', 'audio_translations': 'v1/audio/translations'},
    'validate_backend': {'method': 'GET', 'url':
    'http://infra-inference-scheduling-inference-gateway-istio.dean-ns1.svc.cluste
    r.local:80/health'}}
  ✔ Processor resolved
    Using model 'Qwen/Qwen3-32B' as processor
  ✔ Request loader initialized with inf unique requests
    {'data': "[{'prompt_tokens': 50, 'output_tokens': 50}]", 'data_args': '[]',
    'data_samples': -1, 'preprocessors': ['GenerativeColumnMapper',
    'GenerativeTextCompletionsRequestFormatter'], 'collator':
    'GenerativeRequestCollator', 'sampler': 'None', 'num_workers': 1,
    'random_seed': 42}
  ✔ Resolved transient phase configurations
    Warmup: percent=None value=None mode='prefer_duration'
    Cooldown: percent=None value=None mode='prefer_duration'
    Rampup (Throughput/Concurrent): 0.0
  ✔ AsyncProfile profile resolved
    {'str': "type_='constant' completed_strategies=[] constraints={'max_seconds':
    30} rampup_duration=0.0 strategy_type='constant' rate=[1.0, 5.0]
    max_concurrency=None random_seed=42 strategy_types=['constant', 'constant']",
    'type': 'AsyncProfile', 'class': 'AsyncProfile', 'module':
    'guidellm.benchmark.profiles', 'attributes': {'type_': 'constant',
    'completed_strategies': [], 'constraints': {'max_seconds': 30},
    'rampup_duration': 0.0, 'strategy_type': 'constant', 'rate': [1.0, 5.0],
    'max_concurrency': 'None', 'random_seed': 42}}
  ✔ Output formats resolved
    {'json':
    "output_path=PosixPath('/requests/guidellm_1767024860_rate_comparison_inferenc
    e-scheduling-Qwen3-32B/results.json')"}
  ✔ Setup complete, starting benchmarks...





  ℹ Run Summary Info
  |===========|==========|==========|======|======|======|========|=====|=====|========|=====|=====|
  | Benchmark | Timings                              ||||| Input Tokens     ||| Output Tokens    |||
  | Strategy  | Start    | End      | Dur  | Warm | Cool | Comp   | Inc | Err | Comp   | Inc | Err |
  |           |          |          | Sec  | Sec  | Sec  | Tot    | Tot | Tot | Tot    | Tot | Tot |
  |-----------|----------|----------|------|------|------|--------|-----|-----|--------|-----|-----|
  | constant  | 16:14:45 | 16:15:15 | 30.0 | 0.0  | 0.0  | 1450.0 | 0.0 | 0.0 | 1450.0 | 0.0 | 0.0 |
  | constant  | 16:15:16 | 16:15:46 | 30.0 | 0.0  | 0.0  | 7450.0 | 0.0 | 0.0 | 7450.0 | 0.0 | 0.0 |
  |===========|==========|==========|======|======|======|========|=====|=====|========|=====|=====|


  ℹ Text Metrics Statistics (Completed Requests)
  |===========|=======|======|=======|=======|=======|======|=======|=======|=======|=======|========|========|
  | Benchmark | Input Tokens              |||| Input Words               |||| Input Characters             ||||
  | Strategy  | Per Request || Per Second   || Per Request || Per Second   || Per Request  || Per Second     ||
  |           | Mdn   | p95  | Mdn   | Mean  | Mdn   | p95  | Mdn   | Mean  | Mdn   | p95   | Mdn    | Mean   |
  |-----------|-------|------|-------|-------|-------|------|-------|-------|-------|-------|--------|--------|
  | constant  | 50.0  | 50.0 | 50.0  | 57.5  | 41.0  | 43.0 | 41.0  | 47.1  | 267.0 | 286.0 | 271.9  | 309.0  |
  | constant  | 50.0  | 50.0 | 249.9 | 271.9 | 41.0  | 42.0 | 204.3 | 221.1 | 262.0 | 287.0 | 1311.4 | 1427.0 |
  |===========|=======|======|=======|=======|=======|======|=======|=======|=======|=======|========|========|
  | Benchmark | Output Tokens             |||| Output Words              |||| Output Characters            ||||
  | Strategy  | Per Request || Per Second   || Per Request || Per Second   || Per Request  || Per Second     ||
  |           | Mdn   | p95  | Mdn   | Mean  | Mdn   | p95  | Mdn   | Mean  | Mdn   | p95   | Mdn    | Mean   |
  |-----------|-------|------|-------|-------|-------|------|-------|-------|-------|-------|--------|--------|
  | constant  | 50.0  | 50.0 | 50.0  | 57.5  | 40.0  | 45.0 | 40.0  | 43.0  | 207.0 | 244.0 | 213.7  | 220.9  |
  | constant  | 50.0  | 50.0 | 249.9 | 271.9 | 40.0  | 47.0 | 200.6 | 208.4 | 197.0 | 250.0 | 995.9  | 1045.7 |
  |===========|=======|======|=======|=======|=======|======|=======|=======|=======|=======|========|========|


  ℹ Request Token Statistics (Completed Requests)
  |===========|======|======|======|======|=======|=======|=======|=======|=========|========|
  | Benchmark | Input Tok  || Output Tok || Total Tok    || Stream Iter  || Output Tok      ||
  | Strategy  | Per Req    || Per Req    || Per Req      || Per Req      || Per Stream Iter ||
  |           | Mdn  | p95  | Mdn  | p95  | Mdn   | p95   | Mdn   | p95   | Mdn     | p95    |
  |-----------|------|------|------|------|-------|-------|-------|-------|---------|--------|
  | constant  | 50.0 | 50.0 | 50.0 | 50.0 | 100.0 | 100.0 | 102.0 | 104.0 | 1.0     | 1.0    |
  | constant  | 50.0 | 50.0 | 50.0 | 50.0 | 100.0 | 100.0 | 102.0 | 104.0 | 1.0     | 1.0    |
  |===========|======|======|======|======|=======|=======|=======|=======|=========|========|


  ℹ Request Latency Statistics (Completed Requests)
  |===========|=========|========|======|======|=====|=====|=====|=====|
  | Benchmark | Request Latency || TTFT       || ITL      || TPOT     ||
  | Strategy  | Sec             || ms         || ms       || ms       ||
  |           | Mdn     | p95    | Mdn  | p95  | Mdn | p95 | Mdn | p95 |
  |-----------|---------|--------|------|------|-----|-----|-----|-----|
  | constant  | 0.1     | 0.1    | 14.9 | 17.2 | 2.2 | 2.3 | 2.4 | 2.6 |
  | constant  | 0.1     | 0.1    | 9.8  | 14.4 | 2.2 | 2.3 | 2.3 | 2.6 |
  |===========|=========|========|======|======|=====|=====|=====|=====|


  ℹ Server Throughput Statistics
  |===========|=====|======|=======|======|=======|=======|========|=======|=======|=======|
  | Benchmark | Requests               |||| Input Tokens || Output Tokens || Total Tokens ||
  | Strategy  | Per Sec   || Concurrency || Per Sec      || Per Sec       || Per Sec      ||
  |           | Mdn | Mean | Mdn   | Mean | Mdn   | Mean  | Mdn    | Mean  | Mdn   | Mean  |
  |-----------|-----|------|-------|------|-------|-------|--------|-------|-------|-------|
  | constant  | 1.0 | 1.0  | 0.0   | 0.1  | 50.0  | 57.5  | 2.2    | 57.2  | 113.3 | 114.4 |
  | constant  | 5.0 | 5.0  | 1.0   | 0.6  | 250.1 | 271.8 | 457.4  | 270.7 | 463.2 | 541.4 |
  |===========|=====|======|=======|======|=======|=======|========|=======|=======|=======|



  ✔ Benchmarking complete, generated 2 benchmark(s)
  …   json    :
  /requests/guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B/re
  sults.json
  Harness completed successfully.
  Converting results.json
  Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method.Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method.Results data conversion completed successfully.
  Harness completed: /usr/local/bin/guidellm-llm-d-benchmark.sh
  Running analysis: /usr/local/bin/guidellm-analyze_results.sh
  Done. Data is available at "/requests/guidellm_1767024860_rate_comparison_inference-scheduling-Qwen3-32B"

  ===> Mon Dec 29 18:15:51 IST 2025 - ./run_only.sh:63
  ℹ️ Benchmark workload rate_comparison complete.
  ------------------------------------------------------------

  ===> Mon Dec 29 18:15:51 IST 2025 - ./run_only.sh:63
  ✅
    Experiment ID is 1767024860.
    All workloads completed.
    Results should be available in PVC workload-pvc.
    Please use analyze.sh to fetch and analyze results.

  ------------------------------------------------------------
  ... via 🐍 v3.13.7 (.venv) took 1m30s
  ✦ ❯
  ```

</details>

### Output folder

The output files are saved on the benchmark PVC. They are accessible through the launcher pod in the `/requests` folder. Each experiment is saved under its own sub directory.

This example uses `inference-perf` with a [`shared-prefix`](./inference_scheduling_shared_prefix_template.yaml) workload:

  ```bash
  export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/inference_scheduling_shared_prefix_template.yaml
  ```

After running With this template, the `/requests` folder will include a `<results-folder>` named

```bash
inference-perf_1765442721_shared_prefix_synthetic_inference-scheduling-Qwen3-32B
```

The name indicates `inference-perf` was used as harness, the workload was `shared_prefix_synthetic` and the user-defined stack name was `inference-scheduling-Qwen3-32B`.

### Workload file

The harness workload configuration file, as was used, is copied into the the experiment results directory; in this case, `shared_prefix_synthetic.yaml`.

<details>
<summary><b><i>Click</i></b> to view the workload details (<code>shared_prefix_synthetic.yaml</code>)</summary>

  ```yaml
  load:
    type: constant
    stages:
      - rate: 2
        duration: 50
      - rate: 5
        duration: 50
      - rate: 8
        duration: 50
      - rate: 10
        duration: 50
      - rate: 12
        duration: 50
      - rate: 15
        duration: 50
      - rate: 20
        duration: 50
  api:
    type: completion
    streaming: true
  server:
    type: vllm
    model_name: Qwen/Qwen3-32B
    base_url: http://infra-inference-scheduling-inference-gateway.dpikus-ns.svc.cluster.local:80
    ignore_eos: true
  tokenizer:
    pretrained_model_name_or_path: Qwen/Qwen3-32B
  data:
    type: shared_prefix
    shared_prefix:
      num_groups: 32
      num_prompts_per_group: 32
      system_prompt_len: 2048
      question_len: 256
      output_len: 256
  report:
    request_lifecycle:
      summary: true
      per_stage: true
      per_request: true
  storage:
    local_storage:
      path: /requests/inference-perf_1765442721_shared_prefix_synthetic_inference-scheduling-Qwen3-32B
  ```

</details>

### Text reports

All harnesses capture, more or less, the same metrics (e.g., TTFT, TPOT, ITL). However, differet harnesses produce different result files. In our case, `inference-perf` creates a results file for each stage (6 files), a summary file for all stages, and a (huge) details file with per-request metrics.

In this example there are 6 workload stages. For each of these stages, there is a results `json` file in harness-specific format and a standardized benchmark `yaml` report in a harness-agnostic format. In this case, the `inference-perf` benchmark also creates a summary report and a (huge) detailed per-request report. The `analysis` folder includes plots of the same data.

<details>
<summary><b><i>Click</i></b> to view the contents of the experiment directory (after being copied to <code>destination-path=/tmp/test</code>)</summary>

  ```ls
  $ ls -lnR /tmp/test
  .:
  total 131248
  drwxr-xr-x 2 1000 1000      4096 Dec 22 21:46 analysis
  -rw-r--r-- 1 1000 1000      5172 Dec 22 21:46 benchmark_report,_stage_0_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5147 Dec 22 21:46 benchmark_report,_stage_1_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5152 Dec 22 21:46 benchmark_report,_stage_2_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5149 Dec 22 21:46 benchmark_report,_stage_3_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5134 Dec 22 21:46 benchmark_report,_stage_4_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5152 Dec 22 21:46 benchmark_report,_stage_5_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      5131 Dec 22 21:46 benchmark_report,_stage_6_lifecycle_metrics.json.yaml
  -rw-r--r-- 1 1000 1000      1379 Dec 22 21:46 config.yaml
  -rw-r--r-- 1 1000 1000 134211261 Dec 22 21:46 per_request_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000       913 Dec 22 21:46 shared_prefix_synthetic.yaml
  -rw-r--r-- 1 1000 1000      4503 Dec 22 21:46 stage_0_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4482 Dec 22 21:46 stage_1_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4484 Dec 22 21:46 stage_2_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4487 Dec 22 21:46 stage_3_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4469 Dec 22 21:46 stage_4_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4488 Dec 22 21:46 stage_5_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000      4470 Dec 22 21:46 stage_6_lifecycle_metrics.json
  -rw-r--r-- 1 1000 1000     32786 Dec 22 21:46 stderr.log
  -rw-r--r-- 1 1000 1000      5517 Dec 22 21:46 stdout.log
  -rw-r--r-- 1 1000 1000      4372 Dec 22 21:46 summary_lifecycle_metrics.json

  ./analysis:
  total 272
  -rw-r--r-- 1 1000 1000 90845 Dec 22 21:46 latency_vs_qps.png
  -rw-r--r-- 1 1000 1000 92088 Dec 22 21:46 throughput_vs_latency.png
  -rw-r--r-- 1 1000 1000 89975 Dec 22 21:46 throughput_vs_qps.png
  ```

</details>
<details>

<summary><b><i>Click</i></b> for sample contents of the overall summary file (<code>summary_lifecycle_metrics.json</code>)</summary>

  ```json
  $ cat /tmp/test/summary_lifecycle_metrics.json
  {
    "load_summary": {
      "count": 3600,
      "schedule_delay": {
        "mean": 0.0005517881022468726,
        "min": -0.0009677917696535587,
        "p0.1": -0.0009261268951522652,
        "p1": -0.0006993622815934941,
        "p5": -0.00036710660060634837,
        "p10": -0.0001909452490508556,
        "p25": 0.0001798685480025597,
        "median": 0.0005617527785943821,
        "p75": 0.0009258257632609457,
        "p90": 0.0012554632412502542,
        "p95": 0.0014667386640212496,
        "p99": 0.001798744505795184,
        "p99.9": 0.002180055778066162,
        "max": 0.0024819674144964665
      }
    },
    "successes": {
      "count": 3600,
      "latency": {
        "request_latency": {
          "mean": 5.018124350203054,
          "min": 3.8039849390042946,
          "p0.1": 3.8743090889458545,
          "p1": 3.953152789860906,
          "p5": 4.157410570966022,
          "p10": 4.314808080092189,
          "p25": 4.572383339735097,
          "median": 4.921685875495314,
          "p75": 5.447979573007615,
          "p90": 5.874509802411194,
          "p95": 6.23043658035167,
          "p99": 6.898871109607862,
          "p99.9": 7.155581975026522,
          "max": 7.1762416810088325
        },
        "normalized_time_per_output_token": {
          "mean": 0.03036492437930885,
          "min": 0.007583161046942186,
          "p0.1": 0.010184849169221934,
          "p1": 0.015474372415765174,
          "p5": 0.01634922173616652,
          "p10": 0.01703432744081125,
          "p25": 0.018072373271508013,
          "median": 0.019470786924703526,
          "p75": 0.02165297018970986,
          "p90": 0.023846092055134705,
          "p95": 0.0264264020348377,
          "p99": 0.4516598727074727,
          "p99.9": 0.5963934792099037,
          "max": 1.7235658823337872
        },
        "time_per_output_token": {
          "mean": 0.009651459220661362,
          "min": 0.007256438481493456,
          "p0.1": 0.007323875762569309,
          "p1": 0.007555858870462394,
          "p5": 0.007952370352942776,
          "p10": 0.008277119267264429,
          "p25": 0.008787224616948537,
          "median": 0.009469471096488253,
          "p75": 0.010491207710992169,
          "p90": 0.01131961066218535,
          "p95": 0.012025573761348197,
          "p99": 0.013288400162375152,
          "p99.9": 0.013780982321925176,
          "max": 0.013837818343071613
        },
        "time_to_first_token": {
          "mean": 0.055621399055906094,
          "min": 0.03300976799800992,
          "p0.1": 0.03474102120747557,
          "p1": 0.03800362152425805,
          "p5": 0.04069916713197017,
          "p10": 0.04262162119266577,
          "p25": 0.0469227115099784,
          "median": 0.05276561250502709,
          "p75": 0.05953402600425761,
          "p90": 0.06766136341320816,
          "p95": 0.0781531358021311,
          "p99": 0.1207716233390948,
          "p99.9": 0.1945496345278814,
          "max": 0.28962455500732176
        },
        "inter_token_latency": {
          "mean": 0.009651459220661362,
          "min": 1.1920055840164423e-06,
          "p0.1": 1.969980075955391e-06,
          "p1": 4.66001802124083e-06,
          "p5": 5.8680016081780195e-06,
          "p10": 7.030001142993569e-06,
          "p25": 1.3053999282419682e-05,
          "median": 4.527249257080257e-05,
          "p75": 0.018683608002902474,
          "p90": 0.021172565198503433,
          "p95": 0.023382977170695075,
          "p99": 0.031130830741021784,
          "p99.9": 0.05123655588901624,
          "max": 0.164861869008746
        }
      },
      "throughput": {
        "input_tokens_per_sec": 22133.556296544484,
        "output_tokens_per_sec": 2253.085197994428,
        "total_tokens_per_sec": 24386.64149453891,
        "requests_per_sec": 9.129153381242592
      },
      "prompt_len": {
        "mean": 2424.491666666667,
        "min": 2387.0,
        "p0.1": 2387.0,
        "p1": 2390.0,
        "p5": 2399.0,
        "p10": 2403.0,
        "p25": 2413.0,
        "median": 2426.0,
        "p75": 2435.0,
        "p90": 2443.0,
        "p95": 2450.0,
        "p99": 2468.0,
        "p99.9": 2474.0,
        "max": 2474.0
      },
      "output_len": {
        "mean": 246.8011111111111,
        "min": 3.0,
        "p0.1": 8.599,
        "p1": 11.0,
        "p5": 238.0,
        "p10": 248.0,
        "p25": 253.0,
        "median": 255.0,
        "p75": 256.0,
        "p90": 256.0,
        "p95": 256.0,
        "p99": 257.0,
        "p99.9": 511.0,
        "max": 511.0
      }
    },
    "failures": {
      "count": 0,
      "request_latency": null,
      "prompt_len": null
    }
  }
  ```

</details>

### Standardized benchmark report

To allow easier comparisson between results from different harnesses, the benchmark tools analyze the proprietary formats to produce a standardized report with the common metrics for each experiment stage. For more details, see [Benchmark Report](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md).

### Graphical report

Some harnesses also generate plots of the results. In our example, `inference-perf` generates several plots under the `analysis` sub directory. In [llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) there are examples of more complex plots (see [analysis.ipynb](https://github.com/llm-d/llm-d-benchmark/blob/main/analysis/analysis.ipynb)).

---

## Advanced

### Customizing the config file

This section describes the details of the configuration `config.yaml` file. You may edit it as needed to match your stack (e.g., to change the model name). If you followed the guideline to create your stack then you should be able to run without any modification.
**Do not edit** unless you know what you are doing.

The configuration is divided into sections, each with a different scope.

### Endpoint

These are the properties of the stack (`envsubst` would replace `NAMESPACE` and `GATEWAY_SVC` to match your env). Gated models need a Hugging Face token to access. Your stack should already have a token secret under the name `llm-d-hf-token`. `stack_name` is a user-defined arbitrary name that will be attached to the benchmark results. You can use `stack_name` to help you identify the results of different experiments. The `model` must match your stack. Please note the `yaml` tags -- other section of this `yaml` reference them (e.g., the tokenizer reference the model).

  ```yaml
  endpoint:
    stack_name: &stack_name inference-scheduling-Qwen3-32B  # user defined name for the stack (results prefix)
    model: &model Qwen/Qwen3-32B                      # Exact HuggingFace model name. Must match stack deployed.
    namespace: &namespace $NAMESPACE
    base_url: &url http://${GATEWAY_SVC}.${NAMESPACE}.svc.cluster.local:80  # Base URL of inference endpoint
    hf_token_secret: llm-d-hf-token   # The name of secret that contains the HF token of the stack
  ```

### Control

These define the local target directory for temporary files and for fetching results.
The `kubectl` entry allows you to change the k8s control command (e.g., to `oc`).

  ```yaml
  control:
    work_dir: $HOME/llm-d-bench-work  # working directory to store temporary and autogenerated files.
                                      # Do not edit content manually.
                                      # If not set, a temp directory will be created.
    kubectl: kubectl                  # kubectl command: kubectl or oc
  ```

### Harness

Harness refers to the specific benchmarking tool used. Several harnesses are supported, including
[inference-perf](https://github.com/kubernetes-sigs/inference-perf),
[guidellm](https://github.com/vllm-project/guidellm),
[InferenceMAX](https://github.com/InferenceMAX/InferenceMAX) and
[vLLM Benchmarks](https://github.com/vllm-project/vllm/tree/main/benchmarks).
The `results_pvc` should be set to the PVC you created above.
The benchmark is run from one or more pods inside the cluster.
The image for this pod is from [llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark).
Typically, you do not have to change the `namespace` or the `image`

  ```yaml
  harness:
    name: &harness_name inference-perf
    results_pvc: ${BENCHMARK_PVC}   # PVC where benchmark results are stored
    namespace: *namespace           # Namespace where harness is deployed. Typically with stack.
    parallelism: 1                  # Number of parallel workload launcher pods to create.
    wait_timeout: 600               # Time (in seconds) to wait for workload launcher pod to complete before terminating.
                                    # Set to 0 to disable timeout.
    image: ghcr.io/llm-d/llm-d-benchmark:v0.4.0
    # dataset_url: https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json
  ```

### Extra environment variables

This sections allows you to add arbitrary environment variable to the harness pod. This is mostly useful to change behavior for a specific harness. For example, limit the number of threads the Rust Rayon thread pool should use in inference-perf harness.

  ```yaml
  env:
    - name: RAYON_NUM_THREADS
      value: "4"
  ```

### Workload

These settings characterize of the workload used to benchmark the stack. Each harness supports different configuration parameters for setting the workload. These are described in detail in their documentations (see, e.g., [inference-perf configuration guide](https://github.com/kubernetes-sigs/inference-perf/blob/main/docs/config.md)).
While the details are different for each harness, the concepts are similar.
A workload specification typically includes:

- **Data specification**: How to generated the contents of the inference queries. For example, the distribution of input and output lengths or a path to a HF trace.
- **Load specification**: Timing for sending queries. E.g., rate and duration. Some harnesses support "stages", each with its own load specification.
- **Control**: Which API to use, target endpoint, tokenizers, etc.
- **Output**: The types of reports to produce and where to store them. **Do not change** -- the benchmark tools will set these automatically.

Several workload can be specified, each with a different name. The benchmark would run all the workloads against the stack.

  ```yaml
  workload:                         # yaml configuration for harness workload(s)

    # an example workload using random synthetic data
    sanity_random:
      load:
        type: constant
        stages:
        - rate: 1
          duration: 30
      api:
        type: completion
        streaming: true
      server:
        type: vllm
        model_name: *model
        base_url: *url
        ignore_eos: true
      tokenizer:
        pretrained_model_name_or_path: *model
      data:
        type: random
        input_distribution:
          min: 10             # min length of the synthetic prompts
          max: 100            # max length of the synthetic prompts
          mean: 50            # mean length of the synthetic prompts
          std: 10             # standard deviation of the length of the synthetic prompts
          total_count: 100    # total number of prompts to generate to fit the above mentioned distribution constraints
        output_distribution:
          min: 10             # min length of the output to be generated
          max: 100            # max length of the output to be generated
          mean: 50            # mean length of the output to be generated
          std: 10             # standard deviation of the length of the output to be generated
          total_count: 100    # total number of output lengths to generate to fit the above mentioned distribution constraints
        # path: /workload/ShareGPT_V3_unfiltered_cleaned_split.json   # file name should match dataset_url above
      report:
        request_lifecycle:
          summary: true
          per_stage: true
          per_request: true
      storage:
        local_storage:
          path: /workspace

    # an example workload using shared prefix synthetic data
    shared_prefix_synthetic:
      load:
        type: constant
        stages:
        - rate: 2
          duration: 40
        - rate: 5
          duration: 50
        - rate: 8
          duration: 60
      api:
        type: completion
        streaming: true
      server:
        type: vllm
        model_name: *model
        base_url: *url
        ignore_eos: true
      tokenizer:
        pretrained_model_name_or_path: *model
      data:
        type: shared_prefix
        shared_prefix:
          num_groups: 32                # Number of distinct shared prefixes
          num_prompts_per_group: 32     # Number of unique questions per shared prefix
          system_prompt_len: 2048       # Length of the shared prefix (in tokens)
          question_len: 256             # Length of the unique question part (in tokens)
          output_len: 256               # Target length for the model's generated output (in tokens)
      report:
        request_lifecycle:
          summary: true
          per_stage: true
          per_request: true
      storage:
        local_storage:
          path: /workspace
  ```
