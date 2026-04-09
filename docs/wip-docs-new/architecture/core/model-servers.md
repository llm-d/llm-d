# Model Servers - rob

The Model Server is the component that runs inference on a model. llm-d supports vLLM and SGLang as model server backends.

## Functionality

A Model Server loads a model onto one or more accelerators (GPUs, TPUs, etc.) and exposes an OpenAI-compatible API for inference requests. In the llm-d architecture, Model Servers are the compute layer -- they execute the actual prefill and decode steps that generate tokens.

Model Servers are the lowest layer in the llm-d stack:

```
External Traffic
    |
[ Proxy ] <-> [ EPP ]
    |
[ InferencePool ]
    |
[ Model Server (vLLM / SGLang) ]  <-- runs inference
    |
[ Accelerator (GPU / TPU) ]
```

Model Servers are deployed independently from the rest of the llm-d stack. They join an `InferencePool` automatically via Kubernetes label selectors, and the EPP begins routing traffic to them once they are healthy.

Key responsibilities:

- **Serve inference requests** via an OpenAI-compatible API (`/v1/completions`, `/v1/chat/completions`)
- **Expose metrics** (KV-cache utilization, queue depth, active requests) that the EPP uses for intelligent scheduling
- **Manage KV-cache** on GPU memory, including prefix caching for repeated prompt prefixes
- **Support parallelism strategies** such as Tensor Parallelism (TP), Data Parallelism (DP), and Expert Parallelism (EP) for large models

## Design

### Supported Backends

#### vLLM

[vLLM](https://docs.vllm.ai/) is the primary model server used in llm-d. It provides:

- High-throughput serving with PagedAttention for efficient KV-cache management
- Prefix caching for reduced Time To First Token (TTFT) on repeated prefixes
- Tensor Parallelism, Data Parallelism, and Expert Parallelism for large models
- KV-Events publishing over ZeroMQ for precise prefix cache-aware routing
- KV-cache transfer via NIXL for prefill/decode disaggregation

#### SGLang

[SGLang](https://github.com/sgl-project/sglang) is an alternative model server supported by llm-d. It provides:

- High-performance inference with RadixAttention
- Prefill/decode disaggregation support with NIXL backend
- OpenAI-compatible API

To use SGLang instead of vLLM, set `export INFERENCE_SERVER=sglang` in your deployment environment.

### Joining an InferencePool

Model Servers are discovered dynamically by the InferencePool via Kubernetes label selectors. Apply the matching labels to your Model Server Pod template:

```yaml
labels:
  llm-d.ai/inference-serving: "true"
```

Once labels match, the Model Server Pods automatically appear as endpoints in the InferencePool. The EPP begins collecting metrics and routing traffic to them.

### Health Checks

Model Servers expose health endpoints that Kubernetes uses for liveness and readiness probes:

- **Liveness**: `GET /health` -- confirms the server process is alive
- **Readiness**: `GET /health` -- confirms the server is ready to accept requests

### Parallelism Strategies

For models too large to fit on a single accelerator, llm-d supports several parallelism strategies:

| Strategy | Use Case | Description |
|----------|----------|-------------|
| Tensor Parallelism (TP) | Large models across GPUs within a node | Shards model layers across multiple GPUs |
| Data Parallelism (DP) | Higher throughput on multi-GPU nodes | Runs independent model replicas sharing the same node, each handling different requests |
| Expert Parallelism (EP) | Mixture-of-Experts (MoE) models | Distributes expert layers across GPUs, used for models like DeepSeek-R1 |

These are configured via vLLM/SGLang command-line flags and are transparent to the rest of the llm-d stack.

## Configuration

### vLLM

vLLM is configured via command-line arguments passed to `vllm serve`. Key flags:

| Flag | Description | Example |
|------|-------------|---------|
| `--model` | Model name or path (HuggingFace ID or local path) | `meta-llama/Llama-3.1-8B-Instruct` |
| `--port` | Port to listen on | `8000` |
| `--tensor-parallel-size` | Number of GPUs for tensor parallelism | `4` |
| `--data-parallel-size` | Number of data parallel replicas | `2` |
| `--max-model-len` | Maximum sequence length | `32768` |
| `--block-size` | KV-cache block size (must match EPP `tokenProcessorConfig.blockSize` if using precise prefix cache) | `64` |
| `--enable-prefix-caching` | Enable prefix caching for repeated prompts | (flag) |
| `--gpu-memory-utilization` | Fraction of GPU memory to use for KV-cache | `0.9` |
| `--enable-metrics` | Enable Prometheus metrics endpoint | (flag) |
| `--otlp-traces-endpoint` | OpenTelemetry endpoint for distributed tracing | `http://otel-collector:4318/v1/traces` |

#### KV-Events (for Precise Prefix Cache)

To enable KV-Events publishing for the [KV-Cache Indexer](../advanced/kv-indexer.md):

| Flag | Description |
|------|-------------|
| `--kv-events-config` | JSON configuration for KV-Events publishing |
| `--block-size` | Must match the indexer's `tokenProcessorConfig.blockSize` |

KV-Events config JSON:

```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://gaie-<release>-epp.<namespace>.svc.cluster.local:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

#### Prefill/Decode Disaggregation

For P/D disaggregation, separate prefill and decode workers are deployed with KV-cache transfer configuration:

- **Prefill workers**: Handle the initial prompt processing (prefill phase)
- **Decode workers**: Handle autoregressive token generation (decode phase)
- KV-cache state is transferred between workers via NIXL

### SGLang

SGLang is configured via command-line arguments to `python3 -m sglang.launch_server`. Key flags:

| Flag | Description | Example |
|------|-------------|---------|
| `--model-path` | Model name or path | `meta-llama/Llama-3.1-8B-Instruct` |
| `--port` | Port to listen on | `8000` |
| `--tp` | Tensor parallelism degree | `4` |
| `--dp` | Data parallelism degree | `2` |
| `--context-length` | Maximum context length | `32000` |
| `--enable-metrics` | Enable Prometheus metrics | (flag) |
| `--disaggregation-mode` | Role in P/D disaggregation (`prefill` or `decode`) | `prefill` |
| `--disaggregation-transfer-backend` | KV transfer backend | `nixl` |

### Kubernetes Deployment

A basic Model Server deployment requires:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-model-server
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm
  template:
    metadata:
      labels:
        app: vllm
        llm-d.ai/inference-serving: "true"
    spec:
      containers:
        - name: vllm
          image: <vllm-image>
          command:
            - vllm
            - serve
            - <model-name>
            - --port
            - "8000"
          ports:
            - containerPort: 8000
              name: http
          livenessProbe:
            httpGet:
              path: /health
              port: http
          readinessProbe:
            httpGet:
              path: /health
              port: http
          resources:
            limits:
              nvidia.com/gpu: "1"
```

## Examples

### Basic vLLM Deployment

Serve Llama 3.1 8B with prefix caching enabled:

```yaml
containers:
  - name: vllm
    image: vllm/vllm-openai:latest
    command:
      - vllm
      - serve
      - meta-llama/Llama-3.1-8B-Instruct
      - --port=8000
      - --enable-prefix-caching
      - --gpu-memory-utilization=0.9
      - --enable-metrics
    ports:
      - containerPort: 8000
        name: http
    resources:
      limits:
        nvidia.com/gpu: "1"
```

### vLLM with KV-Events for Precise Prefix Cache

Enable KV-Events publishing for real-time cache-aware routing:

```yaml
containers:
  - name: vllm
    image: vllm/vllm-openai:latest
    command:
      - vllm
      - serve
      - Qwen/Qwen3-32B
      - --port=8000
      - --block-size=64
      - --enable-prefix-caching
      - --gpu-memory-utilization=0.9
      - --kv-events-config
      - |
        {
          "enable_kv_cache_events": true,
          "publisher": "zmq",
          "endpoint": "tcp://gaie-release-epp.default.svc.cluster.local:5557",
          "topic": "kv@$(POD_IP):8000@Qwen/Qwen3-32B"
        }
    resources:
      limits:
        nvidia.com/gpu: "4"
```

### SGLang with P/D Disaggregation

Deploy SGLang as a prefill worker:

```yaml
containers:
  - name: sglang
    image: <sglang-image>
    command:
      - python3
      - -m
      - sglang.launch_server
      - --model-path=meta-llama/Llama-3.1-8B-Instruct
      - --port=8000
      - --context-length=32000
      - --enable-metrics
      - --disaggregation-mode=prefill
      - --disaggregation-transfer-backend=nixl
```

### Multi-GPU Tensor Parallel Deployment

Serve a large model across 4 GPUs with tensor parallelism:

```yaml
containers:
  - name: vllm
    image: vllm/vllm-openai:latest
    command:
      - vllm
      - serve
      - meta-llama/Llama-3.1-70B-Instruct
      - --port=8000
      - --tensor-parallel-size=4
      - --enable-prefix-caching
      - --gpu-memory-utilization=0.9
    resources:
      limits:
        nvidia.com/gpu: "4"
```

## Further Reading

- [vLLM Documentation](https://docs.vllm.ai/)
- [SGLang Documentation](https://github.com/sgl-project/sglang)
- [InferencePool](inferencepool.md) -- how Model Servers are discovered and managed
- [EPP](epp.md) -- how the scheduler routes requests to Model Servers
