# Intelligent Inference Scheduling

vLLM-aware load-balancing enables smarter request routing that improves SLOs.

Traditional Kubernetes services are built around HTTP requests that are fast, uniform, and cheap -- naive round-robin distributes load evenly. LLM requests break all three assumptions: they are slow (a single request can take over a minute generating tokens), non-uniform (a short prompt with a long generation or a RAG prompt with thousands of context tokens and a short answer), and exhibit strong temporal locality (multi-turn conversations and agentic tool loops send the same growing prefix repeatedly). Round-robin ignores these properties and leaves significant performance on the table.

The **Endpoint Picker (EPP)** replaces naive load balancing with LLM-aware scheduling. It scores each candidate model server using real-time signals and routes each request to the best available pod. The scoring pipeline chains weighted plugins -- `prefix-cache-scorer`, `queue-depth-scorer`, `kv-cache-utilization-scorer` -- with configurable weights (default 2:2:1). The EPP connects to the Gateway via Envoy's ext-proc protocol on port 9002, receives the prompt, evaluates the pipeline, and returns the selected pod address. An **InferencePool** CRD defines the set of model server pods via label selectors, and an **HTTPRoute** CRD binds the Gateway to the InferencePool.

## Architecture

### Prefix-Aware Routing

![Prefix-Aware Routing](./images/prefix-aware-routing.svg)

The EPP maintains a view of each pod's prefix-cache state. When a request arrives, it identifies which pod already holds the matching prefix in KV-cache and routes the request there, skipping prompt recomputation entirely. The default mode uses an **approximate prefix-cache scorer** that observes request traffic patterns and maintains a probabilistic hash of cached blocks per server (configurable block size: 64 tokens, LRU capacity: 31,250 entries per server). For workloads with high prefix sharing -- RAG pipelines, multi-tenant deployments with shared system prompts -- a **precise prefix-cache scorer** replaces estimation with exact knowledge by pulling real-time KV-cache state from vLLM via KV-Events (ZMQ on port 5557).

### Load-Aware Routing

![Load-Aware Routing](./images/load-aware-routing.svg)

The EPP continuously probes each pod's metrics via a PodMonitor scraping `/metrics` at 30-second intervals. It scores pods on queue depth, running requests, and KV-cache utilization to route requests to the pod with the lowest load, avoiding hotspots caused by heterogeneous request patterns. This is critical when multiple tenants hit the same endpoint with different request profiles -- without load awareness, a pod receiving a burst of long-generation requests becomes overloaded while others sit idle.

## Guide

See the [Intelligent Inference Scheduling guide](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling) for step-by-step deployment.
