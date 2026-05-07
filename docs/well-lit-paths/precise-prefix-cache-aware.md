# Precise Prefix Cache Aware Routing

The model server is the most accurate source of truth for what's cached on its own GPUs and memory tiers. vLLM, SGLang and NVIDIA TensorRT-LLM publish every cache change as an event; llm-d subscribes to that stream, builds a near-real-time view of resident blocks across the fleet, and scores requests against it. The prefix-affinity score is combined with the standard load-aware scorers, similarly to the [Optimized Baseline](optimized-baseline.md) path.

KV-events have become the ecosystem-standard substrate for exposing accurate cache state — where reusable inference state lives and how it changes over time.
As KV-cache orchestration grows more sophisticated and agentic workloads stretch prefixes longer, cache state becomes something the control plane needs to observe and act on. The same view scales naturally to:
- tier-aware cache tracking across GPU HBM, CPU DRAM, local NVMe, and shared storage;
- policies that account for explicit prompt-cache placement and dynamic KV-offloading;
- cache movement and prefetching workflows for fleet-wide KV reuse;
- advanced KV retention and eviction policies for agentic patterns;
- hybrid-attention models where layer groups (full, sliding-window, linear) evict independently.

## Deploy

See the [precise prefix cache-aware guide](https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-aware) for manifests and step-by-step deployment.

## Architecture

The split is straightforward: **model servers** produce KV-events on every cache change; the **llm-d Router** consumes them to score pods for better routing decisions. The two sides are decoupled — model server and llm-d Router replicas scale independently.

Inside the llm-d Router:
- An **indexer** consumes the event stream and maintains a `block key → pods` mapping for every block resident across the fleet.
- A **scorer** derives block keys deterministically from the input and queries the index. It returns the longest consecutive prefix each candidate pod has cached, weighted by tier.

Events flow from model-server pods to the llm-d Router over ZMQ via **pod discovery**: each model-server pod binds its own ZMQ socket and every llm-d Router replica subscribes to every pod independently. All Router replicas converge to the same index, enabling active-active HA out of the box. The reference guide ships with two Router replicas behind one Service by default, scalable down to one for small fleets.

## Further Reading

See [KV-Cache Indexer](../architecture/advanced/kv-management/kv-indexer.md) for the full architecture
