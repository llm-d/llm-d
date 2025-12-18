# Well-lit Path: Intelligent Inference Scheduling

## Overview

This guide deploys the recommended out of the box [scheduling configuration](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md) for most vLLM deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing. This can be run on a single GPU that can load [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B).

This profile defaults to the approximate prefix cache aware scorer, which only observes request traffic to predict prefix cache locality. The [precise prefix cache aware routing feature](../precise-prefix-cache-aware) improves hit rate by introspecting the vLLM instances for cache entries and will become the default in a future release.

## Hardware Requirements

This example out of the box requires 2 GPUs of any supported kind:
- **NVIDIA GPUs**: Any NVIDIA GPU (support determined by the inferencing image used)
- **Intel XPU/GPUs**: Intel Data Center GPU Max 1550 or compatible Intel XPU device
- **TPUs**: Google Cloud TPUs (when using GKE TPU configuration)

**Alternative CPU Deployment**: For CPU-only deployment (no GPUs required), see the [Hardware Backends](#hardware-backends) section for CPU-specific deployment instructions. CPU deployment requires Intel/AMD CPUs with 64 cores and 64GB RAM per replica.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../prereq/infrastructure)
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
- Create a namespace for installation.
  
  ```
  export NAMESPACE=llm-d-inference-scheduler # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)
- [Skip if using standalone-inference-scheduling] Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md)


## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-inference-scheduler` in this example.

**_IMPORTANT:_** When using long namespace names (like `llm-d-inference-scheduler`), the generated pod hostnames may become too long and cause issues due to Linux hostname length limitations (typically 64 characters maximum). It's recommended to use shorter namespace names (like `llm-d`) and set `RELEASE_NAME_POSTFIX` to generate shorter hostnames and avoid potential networking or vLLM startup problems.

### Deploy

```bash
cd guides/inference-scheduling
```

<!-- TABS:START -->
<!-- TAB:GPU deployment  -->

**GPU deployment**
```bash
helmfile apply -n ${NAMESPACE}
```

<!-- TAB:CPU deployment  -->
**CPU-only deployment:**
```bash
helmfile apply -e cpu -n ${NAMESPACE}
```

<!-- TABS:END -->

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=inference-scheduling-2 helmfile apply -n ${NAMESPACE}`

### Inference Request Scheduler and Hardware Options

#### Inference Request Scheduler
<!-- TABS:START -->

<!-- TAB:Gateway Option -->
##### Gateway Option

**_NOTE:_** This uses Istio as the default gateway provider, see [Gateway Options](./README.md#gateway-options) for installing with a specific provider.

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

<!-- TAB: Standalone Option -->
##### Standalone Option
With this option, the inference scheduler is deployed along with a sidecar Envoy proxy instead of a proxy provisioned using the Kubernetes Gateway API.

To deploy as a standalone inference scheduler, use the `-e standalone` flag, ex:

```bash
helmfile apply -e standalone -n ${NAMESPACE}
```

<!-- TABS:END -->

#### Hardware Backends

Currently in the `inference-scheduling` example we suppport configurations for `xpu`, `tpu`, `cpu`, and `cuda` GPUs. By default we use modelserver values supporting `cuda` GPUs, but to deploy on one of the other hardware backends you may use:

```bash
helmfile apply -e xpu  -n ${NAMESPACE} # targets istio as gateway provider with XPU hardware
# or
helmfile apply -e gke_tpu  -n ${NAMESPACE} # targets GKE externally managed as gateway provider with TPU hardware
# or
helmfile apply -e cpu  -n ${NAMESPACE} # targets istio as gateway provider with CPU hardware
```

##### CPU Inferencing
This case expects using 4th Gen Intel Xeon processors (Sapphire Rapids) or later. 

### Install HTTPRoute When Using Gateway option

Follow provider specific instructions for installing HTTPRoute.

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

<!-- TABS:START -->

<!-- TAB:Gateway Option -->
### Gateway option

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME                        NAMESPACE                 REVISION  UPDATED                               STATUS    CHART                     APP VERSION
gaie-inference-scheduling   llm-d-inference-scheduler 1         2025-08-24 11:24:53.231918 -0700 PDT  deployed  inferencepool-v1.2.0-rc.1 v1.2.0-rc.1
infra-inference-scheduling  llm-d-inference-scheduler 1         2025-08-24 11:24:49.551591 -0700 PDT  deployed  llm-d-infra-v1.3.4        v0.3.0
ms-inference-scheduling     llm-d-inference-scheduler 1         2025-08-24 11:24:58.360173 -0700 PDT  deployed  llm-d-modelservice-v0.3.8 v0.3.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                                  READY   STATUS    RESTARTS   AGE
pod/gaie-inference-scheduling-epp-f8fbd9897-cxfvn                     1/1     Running   0          3m59s
pod/infra-inference-scheduling-inference-gateway-istio-6787675b9swc   1/1     Running   0          4m3s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b58lw9   2/2     Running   0          3m55s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bt5f9s   2/2     Running   0          3m55s

NAME                                                         TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/gaie-inference-scheduling-epp                        ClusterIP      10.16.3.151   <none>        9002/TCP,9090/TCP              3m59s
service/gaie-inference-scheduling-ip-18c12339                ClusterIP      None          <none>        54321/TCP                      3m59s
service/infra-inference-scheduling-inference-gateway-istio   LoadBalancer   10.16.1.195   10.16.4.2     15021:30274/TCP,80:32814/TCP   4m3s

NAME                                                                 READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-inference-scheduling-epp                        1/1     1            1           4m
deployment.apps/infra-inference-scheduling-inference-gateway-istio   1/1     1            1           4m4s
deployment.apps/ms-inference-scheduling-llm-d-modelservice-decode    2/2     2            2           3m56s

NAME                                                                           DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-inference-scheduling-epp-f8fbd9897                        1         1         1       4m
replicaset.apps/infra-inference-scheduling-inference-gateway-istio-678767549   1         1         1       4m4s
replicaset.apps/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b8    2         2         2       3m56s
```

<!-- TAB: Standalone Option -->
### Standalone option

- Firstly, you should be able to list all helm releases to view the 2 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME                        NAMESPACE                 REVISION  UPDATED                               STATUS    CHART                     APP VERSION
gaie-inference-scheduling   llm-d-inference-scheduler 1         2025-08-24 11:24:53.231918 -0700 PDT  deployed  inferencepool-v1.2.0-rc.1 v1.2.0-rc.1
ms-inference-scheduling     llm-d-inference-scheduler 1         2025-08-24 11:24:58.360173 -0700 PDT  deployed  llm-d-modelservice-v0.3.8 v0.3.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                                  READY   STATUS    RESTARTS   AGE
pod/gaie-inference-scheduling-epp-f8fbd9897-cxfvn                     1/1     Running   0          3m59s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b58lw9   2/2     Running   0          3m55s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bt5f9s   2/2     Running   0          3m55s

NAME                                                         TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/gaie-inference-scheduling-epp                        ClusterIP      10.16.3.151   <none>        9002/TCP,9090/TCP              3m59s
service/gaie-inference-scheduling-ip-18c12339                ClusterIP      None          <none>        54321/TCP                      3m59s

NAME                                                                 READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-inference-scheduling-epp                        1/1     1            1           4m
deployment.apps/ms-inference-scheduling-llm-d-modelservice-decode    2/2     2            2           3m56s

NAME                                                                           DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-inference-scheduling-epp-f8fbd9897                        1         1         1       4m
replicaset.apps/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b8    2         2         2       3m56s
```
**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

<!-- TABS:END -->

## Using the stack

For instructions on getting started making inference requests see [our docs](../../docs/getting-started-inferencing.md)

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

## Benchmarking

This section guides you through benchmarking your llm-d deployment to measure inference performance, identify bottlenecks, and establish baselines for production workloads.

### Prerequisites

- A running llm-d deployment from this guide
- Python 3.9+ installed on your client machine
- `${ENDPOINT}` environment variable set (see [Exposing your gateway](../../docs/getting-started-inferencing.md))

### Installing GuideLLM

[GuideLLM](https://github.com/vllm-project/guidellm) is the recommended benchmarking tool for llm-d deployments. It's designed specifically for LLM inference workloads and provides detailed metrics on throughput, latency, and token generation.

```bash
pip install guidellm
```

### Running Benchmarks

#### Quick Benchmark

Run a basic benchmark to verify your deployment is functioning correctly:

```bash
guidellm benchmark \
  --target "${ENDPOINT}/v1" \
  --model "Qwen/Qwen3-0.6B" \
  --request-type completions \
  --max-requests 100 \
  --max-seconds 60
```

#### Load Testing

To understand how your deployment handles production-like traffic, run a sustained load test:

```bash
guidellm benchmark \
  --target "${ENDPOINT}/v1" \
  --model "Qwen/Qwen3-0.6B" \
  --request-type completions \
  --rate 10 \
  --max-seconds 300 \
  --output-path ./benchmark-results
```

**Parameters:**

- `--rate`: Requests per second (adjust based on your capacity)
- `--max-seconds`: Duration of the benchmark
- `--output-path`: Directory for HTML reports and CSV data

#### Throughput Sweep

To find your deployment's maximum throughput, run a sweep across different request rates:

```bash
for rate in 1 5 10 20 50; do
  echo "Testing at ${rate} req/s..."
  guidellm benchmark \
    --target "${ENDPOINT}/v1" \
    --model "Qwen/Qwen3-0.6B" \
    --request-type completions \
    --rate ${rate} \
    --max-seconds 120 \
    --output-path "./benchmark-rate-${rate}"
done
```

### Key Metrics

GuideLLM reports several important metrics for LLM inference:

| Metric | Description | Target |
|--------|-------------|--------|
| **TTFT** (Time to First Token) | Latency before first token generation begins | Lower is better; critical for interactive use cases |
| **ITL** (Inter-Token Latency) | Average time between consecutive tokens | Lower is better; affects perceived responsiveness |
| **Throughput** | Tokens generated per second | Higher is better; measure of overall capacity |
| **P50/P95/P99 Latency** | Percentile latencies for end-to-end requests | P99 < 2x P50 indicates stable performance |

### Interpreting Results

After running benchmarks, GuideLLM generates:

1. **HTML Report**: Visual summary with charts for latency distributions and throughput
2. **CSV Data**: Raw metrics for analysis in spreadsheets or BI tools
3. **Console Output**: Real-time progress and summary statistics

**Signs of a healthy deployment:**

- Consistent TTFT across request rates (until saturation)
- Linear throughput scaling with replicas
- P99 latency within 2-3x of P50 latency

**Warning signs:**

- TTFT spikes at low request rates (possible cold start issues)
- Non-linear latency increase (saturation or queueing)
- High variance in ITL (GPU memory pressure)

### Comparing Scheduling Strategies

To evaluate the intelligent scheduling benefits, compare against a baseline without the inference scheduler:

```bash
# With intelligent scheduling (default)
guidellm benchmark \
  --target "${ENDPOINT}/v1" \
  --model "Qwen/Qwen3-0.6B" \
  --request-type completions \
  --rate 20 \
  --max-seconds 300 \
  --output-path ./benchmark-with-scheduler

# Directly to a model server (bypass scheduler for comparison)
# Note: Requires port-forwarding directly to a decode pod
kubectl port-forward -n ${NAMESPACE} pod/<decode-pod-name> 8200:8200 &
guidellm benchmark \
  --target "http://localhost:8200/v1" \
  --model "Qwen/Qwen3-0.6B" \
  --request-type completions \
  --rate 20 \
  --max-seconds 300 \
  --output-path ./benchmark-direct
```

### Advanced Benchmarking

#### Custom Prompts

Use a dataset file for more realistic workloads:

```bash
guidellm benchmark \
  --target "${ENDPOINT}/v1" \
  --model "Qwen/Qwen3-0.6B" \
  --request-type completions \
  --data "emulated" \
  --data-args '{"prompt_tokens": 512, "output_tokens": 128}' \
  --max-requests 500 \
  --output-path ./benchmark-custom
```

#### Monitoring During Benchmarks

While running benchmarks, monitor your deployment:

```bash
# In a separate terminal - watch pod resource usage
kubectl top pods -n ${NAMESPACE} --containers

# Watch GPU utilization (if nvidia-smi available in pods)
kubectl exec -n ${NAMESPACE} <decode-pod-name> -c vllm -- nvidia-smi -l 1

# Monitor inference scheduler metrics
kubectl port-forward -n ${NAMESPACE} svc/gaie-inference-scheduling-epp 9090:9090 &
curl -s http://localhost:9090/metrics | grep -E "(request|latency|queue)"
```

### Troubleshooting Benchmark Issues

| Issue | Possible Cause | Solution |
|-------|---------------|----------|
| Connection refused | Gateway not ready | Wait for pods to be Ready, verify `${ENDPOINT}` |
| 503 errors | No healthy backends | Check decode pod status and logs |
| Very high TTFT | Model loading | Allow warm-up requests before benchmarking |
| Inconsistent results | Resource contention | Ensure no other workloads on cluster |

### Next Steps

- For production deployments, establish baseline benchmarks and run regression tests after upgrades
- Compare results across different [hardware backends](#hardware-backends)
- Explore [prefix cache aware routing](../precise-prefix-cache-aware) for improved cache hit rates
- For long-prompt workloads, consider [prefill/decode disaggregation](../pd-disaggregation)
