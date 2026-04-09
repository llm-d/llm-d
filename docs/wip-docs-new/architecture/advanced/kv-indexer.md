THIS NEEDS TO BE UPDATED, WRITTEN BY CLAUDE

# KV-Cache Indexer - [?]

The KV-Cache Indexer enables precise prefix cache-aware routing in llm-d by maintaining a near-real-time view of KV-Cache block distribution across a fleet of Model Servers.

> In comparison to EPP's `prefix-cache-scorer` which maintains an approximate view of the KV cache, the `precise-kv-cache-scorer` leverages **KV-Events** emitted by the Model Servers to maintain a globally consistent view of the KV cache state, which can be useful in near saturation regmies of for multi-modal inputs.

## Functionality

Reusing KV-Cache blocks rather than recomputing them significantly improves both Time To First Token (TTFT) and overall throughput. The KV-Cache Indexer tracks which KV-Cache blocks exist on which model server pods, so the inference scheduler (EPP) can route requests to the pod that already has the most relevant cached blocks.

The indexer is implemented in the [llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) repository and runs as a library embedded in the EPP via the `precise-prefix-cache-scorer` plugin.

## Design

The indexer has two primary data flows:

### Write Path: Ingesting Cache Events

Model servers like vLLM can be configured to emit **KV-Events** over ZeroMQ whenever cache blocks are created or evicted. The indexer subscribes to these events and updates an internal block index in near-real-time.

1. A vLLM pod creates or evicts KV-Cache blocks and publishes an event to a ZMQ topic (format: `kv@<pod-ip>:<port>@<model-name>`)
2. The indexer's event pool receives the message and routes it to a worker using consistent hashing on the pod ID (FNV-1a), ensuring events from the same pod are processed in order
3. The worker decodes the event (msgpack-encoded for vLLM) and updates the block index accordingly

### Read Path: Scoring Pods

When a new inference request arrives, the EPP asks the indexer to score each candidate pod based on how much of the request's prefix is already cached.

1. The incoming prompt is tokenized (via a UDS tokenizer sidecar or HuggingFace tokenizer)
2. Tokens are chunked into fixed-size blocks (e.g., 64 tokens) and hashed into deterministic KV-block keys using chained FNV-64a hashes, matching vLLM's content-addressing logic
3. The indexer looks up which pods have each block key and finds the longest consecutive prefix match from the start
4. Each pod receives a score based on its number of consecutive matching blocks
5. The EPP uses this score (along with other scorers like queue depth and cache utilization) to pick the optimal pod

### Architecture Modules

| Module | Purpose | Default Implementation |
| :--- | :--- | :--- |
| `kvcache.Indexer` | Main orchestrator for scoring requests | Coordinates all internal modules |
| `kvevents.Pool` | Ingests and processes KV-Cache events from vLLM | Sharded worker pool using ZMQ subscription |
| `kvevents.EngineAdapter` | Parses engine-specific raw event messages | vLLM adapter for msgpack-encoded events |
| `kvblock.Index` | Core data store mapping block hashes to pod locations | In-memory two-level LRU cache |
| `kvblock.TokenProcessor` | Converts token sequences into KV-block keys | Chunking and hashing compatible with vLLM |
| `kvblock.Scorer` | Scores pods based on cache hit sequences | Longest consecutive prefix matching |

### Index Backends

The block index supports multiple backends:

- **In-Memory (default)** - Fast, thread-safe, two-level LRU cache using `hashicorp/golang-lru`. Stores up to 100M keys with configurable pod cache size per key. Best for most deployments.
- **Cost-Aware Memory** - Uses `hypermodeinc/ristretto` for cost-aware eviction based on actual memory usage. Useful when memory usage patterns vary significantly across keys.
- **Redis** - Distributed backend shared by multiple indexer replicas. Provides persistence and scalability.
- **Valkey** - Redis-compatible, open-source alternative (BSD licensed). Supports RDMA for reduced latency.

### Event Delivery Modes

The indexer supports two modes for receiving KV-Events from vLLM pods:

#### Centralized ZMQ Endpoint (Default)

All vLLM pods publish events to a single ZMQ endpoint hosted on the EPP. This is simpler to configure and works well for single-scheduler deployments.

**EPP side:** `zmqEndpoint: "tcp://*:5557"` with `discoverPods: false`

**vLLM side:**
```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://gaie-<release>-epp.<namespace>.svc.cluster.local:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

#### Pod Discovery Mode

Each vLLM pod publishes events on its own ZMQ endpoint, and the EPP discovers pods automatically via Kubernetes label selectors. This mode supports active-active multi-scheduler deployments, where each scheduler replica maintains a global view.

**EPP side:** `zmqEndpoint: "tcp://*:5557"` with `discoverPods: true`

**vLLM side:**
```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://*:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

Enable via Helm: `POD_DISCOVERY=true helmfile apply -n ${NAMESPACE}`

### Tokenizer Sidecar

The EPP runs a UDS tokenizer sidecar alongside the inference scheduler to provide fast tokenization without network overhead. The sidecar:

- Downloads and caches tokenizers from HuggingFace
- Exposes tokenization over a Unix Domain Socket at `/tmp/tokenizer/tokenizer-uds.socket`
- Is shared by both the `tokenizer` plugin (for request preprocessing) and the `precise-prefix-cache-scorer` plugin (for block key generation)

## Configuration

The KV-Cache Indexer is configured through the EPP's `EndpointPickerConfig` as parameters to the `precise-prefix-cache-scorer` plugin. The configuration has three top-level sections.

### Token Processor Configuration

Controls how tokens are chunked into KV-Cache blocks.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `blockSize` | integer | `16` | Number of tokens per block. **Must match** vLLM's `--block-size` flag. |
| `hashSeed` | string | `""` | Seed for FNV-64a hashing. **Must align** with `PYTHONHASHSEED` on vLLM pods. |

### Indexer Configuration

Controls the indexer's behavior and tokenizer access.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `speculativeIndexing` | boolean | `false` | Enable speculative indexing to predict prefix cache hits before events arrive. |
| `tokenizersPoolConfig.modelName` | string | - | The model name used for tokenization (e.g., `Qwen/Qwen3-32B`). |
| `tokenizersPoolConfig.workersCount` | integer | `5` | Number of tokenization worker goroutines. |
| `tokenizersPoolConfig.uds.socketFile` | string | - | Path to UDS tokenizer socket. Recommended for production. |
| `tokenizersPoolConfig.hf.enabled` | boolean | `true` | Enable downloading tokenizers from HuggingFace Hub. |
| `tokenizersPoolConfig.hf.huggingFaceToken` | string | `""` | HuggingFace API token for private models. |
| `tokenizersPoolConfig.local.autoDiscoveryDir` | string | `"/mnt/models"` | Directory to scan for local tokenizer files. |

#### Index Backend Configuration

Only one backend should be configured. If multiple are set, see priority in [llm-d-kv-cache docs](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/configuration.md).

**In-Memory (default):**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.inMemoryConfig.size` | integer | `100000000` | Maximum number of stored keys. |
| `kvBlockIndexConfig.inMemoryConfig.podCacheSize` | integer | `10` | Number of pod entries cached per key. |

**Cost-Aware Memory:**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.costAwareMemoryConfig.size` | string | `"2GiB"` | Maximum memory usage (e.g., `500MiB`, `2GiB`). |

**Redis / Valkey:**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.redisConfig.address` | string | `"redis://127.0.0.1:6379"` | Connection URL. Supports `redis://` and `valkey://` schemes. |
| `kvBlockIndexConfig.redisConfig.backendType` | string | `"redis"` | Backend type: `redis` or `valkey`. |
| `kvBlockIndexConfig.redisConfig.enableRDMA` | boolean | `false` | Enable RDMA transport (Valkey only). |

### KV-Events Configuration

Controls how the indexer receives cache events from vLLM pods.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `zmqEndpoint` | string | `""` | ZMQ endpoint to bind/connect. e.g., `tcp://*:5557`. |
| `topicFilter` | string | `"kv@"` | ZMQ topic prefix filter for KV-Cache events. |
| `concurrency` | integer | `4` | Number of parallel event processing workers. |
| `engineType` | string | `"vllm"` | Inference engine type (`vllm` or `sglang`). |
| `discoverPods` | boolean | `true` | Enable Kubernetes pod auto-discovery mode. |

#### Pod Discovery Configuration

When `discoverPods` is `true`, the indexer watches Kubernetes pods and connects to their individual ZMQ endpoints.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `podDiscoveryConfig.podLabelSelector` | string | `"llm-d.ai/inferenceServing=true"` | Label selector for discovering vLLM pods. |
| `podDiscoveryConfig.podNamespace` | string | `""` | Namespace to watch. Empty watches all namespaces. |
| `podDiscoveryConfig.socketPort` | integer | `5557` | ZMQ port on each vLLM pod. |

### vLLM Model Server Requirements

The vLLM model servers must be configured to publish KV-Events:

- **`--block-size`** must match the indexer's `tokenProcessorConfig.blockSize` (e.g., `--block-size=64`)
- **`--kv-events-config`** must enable event publishing with the correct endpoint and topic format

### Scheduling Weights

The `precise-prefix-cache-scorer` is one of several scorers used in the scheduling profile. Typical weights:

| Scorer | Weight | Purpose |
| :--- | :--- | :--- |
| `precise-prefix-cache-scorer` | 3.0 | Prefer pods with cached prefix blocks |
| `kv-cache-utilization-scorer` | 2.0 | Balance load based on GPU memory pressure |
| `queue-scorer` | 2.0 | Prefer pods with shorter request queues |

The higher weight on the prefix cache scorer reflects the significant latency benefit of cache hits -- up to 99.5% reduction in TTFT in benchmarks.

## Examples

### Full EPP Configuration with KV-Cache Indexer

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
  - type: single-profile-handler
  - type: tokenizer
    parameters:
      modelName: Qwen/Qwen3-32B
      udsTokenizerConfig:
        socketFile: /tmp/tokenizer/tokenizer-uds.socket
  - type: precise-prefix-cache-scorer
    parameters:
      tokenProcessorConfig:
        blockSize: 64
      indexerConfig:
        speculativeIndexing: true
        tokenizersPoolConfig:
          modelName: Qwen/Qwen3-32B
          local: null
          hf: null
          uds:
            socketFile: /tmp/tokenizer/tokenizer-uds.socket
      kvEventsConfig:
        topicFilter: "kv@"
        concurrency: 4
        discoverPods: false
        zmqEndpoint: "tcp://*:5557"
  - type: kv-cache-utilization-scorer
  - type: queue-scorer
  - type: max-score-picker
schedulingProfiles:
  - name: default
    plugins:
      - pluginRef: precise-prefix-cache-scorer
        weight: 3.0
      - pluginRef: kv-cache-utilization-scorer
        weight: 2.0
      - pluginRef: queue-scorer
        weight: 2.0
      - pluginRef: max-score-picker
```

### vLLM Model Server with KV-Events

```yaml
args:
  - "--block-size=64"
  - "--kv-events-config"
  - |-
    {
      "enable_kv_cache_events": true,
      "publisher": "zmq",
      "endpoint": "tcp://gaie-<release>-epp.<namespace>.svc.cluster.local:5557",
      "topic": "kv@$(POD_IP):8000@Qwen/Qwen3-32B"
    }
```

### Pod Discovery Mode

For multi-scheduler active-active deployments:

**EPP configuration:**
```yaml
kvEventsConfig:
  topicFilter: "kv@"
  concurrency: 4
  discoverPods: true
  zmqEndpoint: "tcp://*:5557"
  podDiscoveryConfig:
    podLabelSelector: "llm-d.ai/inferenceServing=true"
    socketPort: 5557
```

**vLLM configuration:**
```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://*:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

## Further Reading

- [llm-d-kv-cache: Architecture](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/architecture.md) - Detailed architecture with sequence diagrams
- [llm-d-kv-cache: Configuration Reference](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/configuration.md) - Complete configuration field reference
