# KV-Cache Indexer

The KV-Cache Indexer enables **precise prefix cache-aware routing** in llm-d. It maintains a near-real-time view of which KV-Cache blocks exist on which model server pods, so the EPP can route each inference request to the pod that already has the most relevant cached data -- avoiding redundant prefill computation and significantly reducing Time To First Token (TTFT).

The indexer is implemented in the [llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) repository and runs as a library embedded in the EPP via the `precise-prefix-cache-scorer` plugin.

## What the KV-Cache Indexer Enables

By subscribing to **KV-Events** -- real-time notifications emitted by model servers whenever cache blocks are created or evicted -- the KV-Cache Indexer gives the EPP a globally consistent, event-driven view of the actual cache state across the fleet. This foundation unlocks a family of advanced prefix-cache-aware scheduling capabilities:

- **Multimodal and LoRA-aware routing.** The indexer incorporates multimodal content hashes and adapter identity into block keys, enabling accurate cache-aware routing for vision, audio, and fine-tuned workloads where token IDs alone are insufficient to identify cached content.
- **Speculative indexing.** Drawing on the strengths of approximate prefix-cache tracking, the indexer can speculatively predict cache placement for a request before engine confirmation events arrive -- combining the low-latency benefits of approximation with the accuracy of event-driven state, working towards a single unified prefix-cache scorer.
- **Heterogeneous device-tier and data-parallel awareness.** The indexer tracks cache state across device tiers and data-parallel replicas, weighting scores by storage tier and routing to the specific replica with the best coverage.
- **HMA awareness.** The indexer is aware of partial KV-cache HMA group evictions and its KV-cache hit scoring is accurate.
- **Future-proof.** As KV-cache orchestration becomes more granular and complex for workload-aware inference such as agentic inference, KVEvent preciseness ensures accurate indexing.

## How It Works

The KV-Cache Indexer has two data flows: a **write path** that continuously ingests cache state from model servers, and a **read path** that feeds into the EPP's scheduling pipeline when a new inference request arrives.

```
                         EPP Pod
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  Tokenizer Sidecar         Inference Scheduler                     │
  │  ┌────────────────┐        ┌──────────────────────────────────────┐│
  │  │                │        │                                      ││
  │  │  Render&/ ─────┼──UDS──►│  ┌─────────────┐                     ││
  │  │  Tokenize      │        │  │   Block     │  PrepareData  Score ││
  │  │  Input         │        │  │   Index     │  ┌─────────┐ ┌───┐  ││
  │  └────────────────┘        │  │             │  │Compute  │ │Nor│  ││
  │                            │  │             ├─►│keys,    ├►│mal│  ││
  │                            │  │             │  │look up, │ │ize│  ││
  │                            │  │             │  │pre-score│ │   │  ││
  │                            │  └──────┬──────┘  └─────────┘ └───┘  ││
  │                            │         │                            ││
  │                            │  PreRequest (post-decision)          ││
  │                            │  ┌────────────────────────────────┐  ││
  │                            │  │ Speculatively index chosen pod │  ││
  │                            │  └────────────────────────────────┘  ││
  │                            └──────────────────────────────────────┘│
  └───────────────────────────────────┬────────────────────────────────┘
                                      │ ZMQ
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
              ┌───────────┐    ┌───────────┐     ┌───────────┐
              │  vLLM Pod │    │  vLLM Pod │     │  vLLM Pod │
              │  (events) │    │  (events) │     │  (events) │
              └───────────┘    └───────────┘     └───────────┘
```

### Write Path: Ingesting Cache Events

Model servers like vLLM emit **KV-Events** over ZeroMQ whenever cache blocks are created or evicted. The indexer subscribes to these events and updates its internal block index in near-real-time.

1. A vLLM pod creates or evicts KV-Cache blocks and publishes an event over ZMQ. The message topic follows the format `kv@<pod-ip>:<port>@<model-name>`, with a msgpack-encoded payload containing the block hashes, tokens, and device tier.
2. The indexer's **event pool** receives the message and routes it to a worker shard using FNV-1a hashing on the pod ID. This guarantees that events from the same pod are always processed in order by the same worker.
3. The **engine adapter** (vLLM or SGLang) decodes the payload into domain events: `BlockStored`, `BlockRemoved`, or `AllBlocksCleared`.
4. For stored blocks, the worker computes **request keys** (deterministic hashes of the token content) and maps them alongside the engine-provided **engine keys** in the block index. For removed blocks, the corresponding entries are evicted.

The dual-key design (engine keys from vLLM, request keys computed locally) is important: when a new request arrives, the indexer computes request keys from the prompt tokens and looks them up directly -- without needing to know what engine keys vLLM assigned.

### Read Path: EPP Pipeline Integration

Rather than a single scoring call, the indexer's read-path logic is split across multiple stages of the EPP's scheduling pipeline. The `precise-prefix-cache-scorer` plugin implements three EPP extension points, and works alongside a separate `tokenizer` plugin:

#### 1. Tokenizer Plugin (Scorer -- data producer)

A dedicated **TokenizerPlugin** runs as a scorer that produces data rather than scores. It tokenizes the incoming prompt and writes the token IDs (and any multimodal features) into the scheduling cycle state. It returns zero scores for all endpoints -- its only purpose is to make tokenized data available to downstream plugins without redundant re-tokenization.

#### 2. PrepareData (PrepareDataPlugin)

Before scoring begins, the precise-prefix-cache-scorer's `PrepareRequestData` method runs. It consumes the tokenized prompt from the cycle state and:

1. **Computes block keys** -- chunks tokens into fixed-size blocks (matching vLLM's `--block-size`) and hashes them into deterministic keys using chained FNV-64a over CBOR-encoded `[parentHash, tokenChunk, extra]` tuples, reproducing vLLM's content-addressing scheme.
2. **Looks up the block index** -- queries which pods hold each block key.
3. **Pre-scores endpoints** -- walks the block keys in order and finds the longest consecutive prefix match per pod, weighting each match by device tier. Stores the resulting `PrefixCacheMatchInfo` on each endpoint for the scoring stage.

#### 3. Score (Scorer)

The scoring stage reuses the pre-computed results from PrepareData, normalizes them to the 0.0--1.0 range, and returns them to the EPP scheduler. The EPP combines this score with other weighted scorers (queue depth, KV-cache utilization) through the standard Filter-Score-Pick pipeline.

#### 4. PreRequest (PreRequest -- post-decision)

After the scheduler picks an endpoint, the plugin records **speculative entries** in the block index for the selected pod. This closes the blind spot between when a request is routed and when the model server's KV-Events confirm the blocks were actually created. Speculative entries have a short TTL and are automatically evicted if not confirmed by engine events.

## Scoring Algorithm

The indexer uses a **longest consecutive prefix match** strategy. The intuition is straightforward: KV-Cache blocks form a chain where each block depends on all previous blocks. A pod can only reuse cached blocks if it has an unbroken prefix starting from block 0.

Consider a prompt that produces 5 block keys `[B0, B1, B2, B3, B4]` and three candidate pods:

```
Block keys:   B0    B1    B2    B3    B4

Pod A:        yes   yes   yes   yes   no    → score = 4.0 (longest prefix = 4 blocks)
Pod B:        yes   yes   no    -     -     → score = 2.0 (chain breaks at B2)
Pod C:        no    -     -     -     -     → score = 0.0 (no match from start)
```

Notice that even if Pod C had blocks B3 and B4 cached, it would still score 0 -- those blocks are useless without the preceding chain. This matches how vLLM's prefix caching actually works: the engine can only skip prefill for a contiguous prefix of blocks.

When blocks are stored on different device tiers (GPU vs CPU), each matching block's score contribution is weighted by the tier. The scorer takes the maximum weight per pod across tiers, so a block cached on both GPU and CPU uses the GPU weight.

## Architecture

### Core Modules

| Module | Purpose | Default Implementation |
| :--- | :--- | :--- |
| `kvcache.Indexer` | Main orchestrator -- tokenizes, computes block keys, looks up index, scores | Coordinates all internal modules |
| `kvevents.Pool` | Ingests KV-Cache events from model servers | Sharded worker pool with ZMQ subscribers |
| `kvevents.EngineAdapter` | Parses engine-specific event payloads | vLLM adapter (msgpack) |
| `kvblock.Index` | Maps block hashes to pod locations | In-memory two-level LRU cache |
| `kvblock.TokenProcessor` | Converts token sequences into deterministic block keys | FNV-64a over CBOR-encoded chunks |
| `kvblock.Scorer` | Scores pods based on cache hit patterns | Longest consecutive prefix match |

### Index Backends

The block index supports multiple storage backends. Only one should be configured; if multiple are set, the first available is used.

- **In-Memory (default)** -- Fast, thread-safe, two-level LRU cache (`hashicorp/golang-lru`). The outer LRU maps block hashes to a per-key pod cache; the inner LRU tracks which pods hold that block. Stores up to 100M keys with 10 pod entries per key. Best for most single-scheduler deployments.

- **Cost-Aware Memory** -- Uses `hypermodeinc/ristretto` for memory-budget-aware eviction. Instead of a fixed key count, you specify a memory ceiling (e.g. `2GiB`). Useful when entry sizes vary significantly, such as with multimodal workloads.

- **Redis** -- Distributed backend for multi-replica indexer deployments. Provides persistence and horizontal scalability.

- **Valkey** -- Redis-compatible, open-source alternative (BSD licensed). Supports optional RDMA transport for reduced network latency.

### Event Delivery Modes

The indexer supports two modes for receiving KV-Events from vLLM pods:

#### Centralized ZMQ Endpoint

All vLLM pods publish events to a single ZMQ endpoint hosted by the EPP. Simpler to configure and works well for single-scheduler deployments.

```
  vLLM Pod A ──► ZMQ ──┐
  vLLM Pod B ──► ZMQ ──┼──► EPP (binds tcp://*:5557)
  vLLM Pod C ──► ZMQ ──┘
```

**EPP side:** `zmqEndpoint: "tcp://*:5557"` with `discoverPods: false`

**vLLM side:**
```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://<epp-service>.<namespace>.svc.cluster.local:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

#### Pod Discovery Mode

Each vLLM pod publishes events on its own ZMQ endpoint. The EPP discovers pods automatically via Kubernetes label selectors and creates per-pod ZMQ subscribers. This mode supports **active-active multi-scheduler** deployments, where each EPP replica independently subscribes to all pods and maintains a complete global view.

```
  vLLM Pod A (binds :5557) ◄── ZMQ ──┐
  vLLM Pod B (binds :5557) ◄── ZMQ ──┼── EPP Replica 1 (discovers via K8s labels)
  vLLM Pod C (binds :5557) ◄── ZMQ ──┘

  vLLM Pod A (binds :5557) ◄── ZMQ ──┐
  vLLM Pod B (binds :5557) ◄── ZMQ ──┼── EPP Replica 2 (discovers via K8s labels)
  vLLM Pod C (binds :5557) ◄── ZMQ ──┘
```

**EPP side:** `discoverPods: true`.

**vLLM side:**
```json
{
  "enable_kv_cache_events": true,
  "publisher": "zmq",
  "endpoint": "tcp://*:5557",
  "topic": "kv@<pod-ip>:8000@<model-name>"
}
```

### Tokenizer

The indexer needs a tokenizer to convert prompts into token IDs before computing block keys. It supports three backends, checked in order:

1. **UDS Sidecar** (recommended for production) -- Communicates with a tokenizer sidecar over a Unix Domain Socket at a configurable path (e.g. `/tmp/tokenizer/tokenizer-uds.socket`). The sidecar downloads and caches tokenizers from HuggingFace and is shared with the EPP's `tokenizer` plugin.
2. **Local files** -- Auto-discovers `tokenizer.json` files in a configured directory (e.g. `/mnt/models`). Supports HuggingFace cache layout (`models--org--model/snapshots/{hash}/tokenizer.json`) and flat layouts.
3. **HuggingFace Hub** -- Downloads tokenizers directly. Useful for development but adds startup latency.

### Multimodal Support

For multimodal inputs (images, audio), the indexer incorporates **extra features** into block key hashes. When a prompt contains multimodal placeholders, each affected block receives an additional hash component derived from the multimodal content hashes. This ensures that two prompts with the same text tokens but different images produce different block keys -- and route to the pod that has the correct multimodal KV cache.

## Configuration

The KV-Cache Indexer is configured as parameters to the `precise-prefix-cache-scorer` plugin in the EPP's `EndpointPickerConfig`. The configuration has three top-level sections.

### Token Processor

Controls how tokens are chunked into KV-Cache blocks and hashed.

| Field       | Type    | Default | Description                    |
|:------------|:--------|:--------|:-------------------------------|
| `blockSize` | integer | `16`    | Number of tokens per block.    |
| `hashSeed`  | string  | `""`    | Seed for FNV-64a initial hash. |

### Indexer

Controls tokenizer access and optional features.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `speculativeIndexing` | boolean | `false` | Enable speculative indexing to predict prefix cache hits before engine confirmation events arrive. |
| `tokenizersPoolConfig.modelName` | string | - | Model name for tokenization (e.g. `Qwen/Qwen3-32B`). |
| `tokenizersPoolConfig.workersCount` | integer | `5` | Number of tokenization worker goroutines. |
| `tokenizersPoolConfig.uds.socketFile` | string | - | Path to UDS tokenizer socket. Recommended for production. |
| `tokenizersPoolConfig.hf.enabled` | boolean | `true` | Enable downloading tokenizers from HuggingFace Hub. |
| `tokenizersPoolConfig.hf.huggingFaceToken` | string | `""` | HuggingFace API token for gated or private models. |
| `tokenizersPoolConfig.local.autoDiscoveryDir` | string | `"/mnt/models"` | Directory to scan for local tokenizer files. |

### Index Backend

Only one backend should be configured. If multiple are present, the first available is used (see [configuration reference](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/configuration.md) for priority order).

**In-Memory (default):**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.inMemoryConfig.size` | integer | `100000000` | Maximum number of stored block keys. |
| `kvBlockIndexConfig.inMemoryConfig.podCacheSize` | integer | `10` | Maximum number of pod entries per block key. |

**Cost-Aware Memory:**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.costAwareMemoryConfig.size` | string | `"2GiB"` | Memory budget (e.g. `500MiB`, `2GiB`). |

**Redis / Valkey:**

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvBlockIndexConfig.redisConfig.address` | string | `"redis://127.0.0.1:6379"` | Connection URL. Supports `redis://` and `valkey://` schemes. |
| `kvBlockIndexConfig.redisConfig.backendType` | string | `"redis"` | Backend type: `redis` or `valkey`. |
| `kvBlockIndexConfig.redisConfig.enableRDMA` | boolean | `false` | Enable RDMA transport (Valkey only). |

### KV-Events

Controls how the indexer receives cache events from model servers.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `zmqEndpoint` | string | `""` | ZMQ endpoint to bind/connect (e.g. `tcp://*:5557`). |
| `topicFilter` | string | `"kv@"` | ZMQ topic prefix filter for KV-Cache events. |
| `concurrency` | integer | `4` | Number of parallel event processing workers. |
| `engineType` | string | `"vllm"` | Inference engine type: `vllm` or `sglang`. |
| `discoverPods` | boolean | `true` | Enable Kubernetes pod auto-discovery mode. |

**Pod Discovery** (when `discoverPods` is `true`):

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `podDiscoveryConfig.podLabelSelector` | string | `"llm-d.ai/inferenceServing=true"` | Label selector for discovering model server pods. |
| `podDiscoveryConfig.podNamespace` | string | `""` | Namespace to watch. Empty watches all namespaces. |
| `podDiscoveryConfig.socketPort` | integer | `5557` | ZMQ port on each model server pod. |

### Device Tier Weights

Controls how different storage tiers contribute to scoring.

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `kvCacheBackendConfigs[].name` | string | - | Device tier name (e.g. `gpu`, `cpu`). |
| `kvCacheBackendConfigs[].weight` | float | `1.0` | Scoring weight for blocks on this tier. |

Default configuration: `gpu` = 1.0, `cpu` = 0.8.

### vLLM Requirements

The vLLM model servers must be configured to publish KV-Events:

- **`--block-size`** must match the indexer's `tokenProcessorConfig.blockSize` (e.g. `--block-size=16`)
- **`--kv-events-config`** must enable event publishing with the correct endpoint and topic format
- **`PYTHONHASHSEED`** should align with the indexer's `hashSeed` for deterministic block key matching

### Scheduling Weights

The `precise-prefix-cache-scorer` is one of several scorers in the scheduling profile. Typical weights:

| Scorer | Weight | Purpose |
| :--- | :--- | :--- |
| `precise-prefix-cache-scorer` | 3.0 | Prefer pods with cached prefix blocks |
| `kv-cache-utilization-scorer` | 2.0 | Balance load based on GPU memory pressure |
| `queue-scorer` | 2.0 | Prefer pods with shorter request queues |

The higher weight on the prefix cache scorer reflects the significant latency benefit of cache hits.

## Examples

### EPP Configuration with KV-Cache Indexer

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
        blockSize: 16
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

### vLLM with KV-Events (Centralized)

```yaml
args:
  - "--block-size=16"
  - "--kv-events-config"
  - |-
    {
      "enable_kv_cache_events": true,
      "publisher": "zmq",
      "endpoint": "tcp://<epp-service>.<namespace>.svc.cluster.local:5557",
      "topic": "kv@$(POD_IP):8000@Qwen/Qwen3-32B"
    }
```

### Pod Discovery Mode (Multi-Scheduler)

For active-active multi-scheduler deployments where each EPP replica needs a global view:

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

- [llm-d-kv-cache: Architecture](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/architecture.md) -- Detailed architecture with sequence diagrams
- [llm-d-kv-cache: Configuration Reference](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/configuration.md) -- Complete configuration field reference
- [EPP Scheduling](../core/epp/scheduling.md) -- How scorers fit into the Filter-Score-Pick pipeline
