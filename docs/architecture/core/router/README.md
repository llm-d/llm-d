# llm-d Router

The **llm-d Router** is the intelligent entry point for inference requests in the llm-d stack. It provides sophisticated, LLM-aware load balancing, request queuing, and policy enforcement without reimplementing a full-featured network proxy.

## Composition

The llm-d Router is composed of two primary functional parts:

### Proxy 
Any conformant industry-grade L7 proxy (typically Envoy). The proxy handles the data plane, including connection management, TLS termination, and request forwarding.

See the [**Proxy deep dive**](proxy.md) to learn about deployment modes (Standalone vs. Gateway Mode), request flow, and Gateway API integration.

### llm-d Endpoint Picker (EPP) 

A specialized service that the proxy consults for every request. The [EPP](epp/README.md) contains the routing "intelligence," using real-time signals from model servers to make optimal placement decisions.

See the [**llm-d EPP deep dive**](epp/README.md) to learn about the the routing engine's architecture, plugin pipeline (Filters, Scorers, Pickers), and flow control mechanisms.

## How it Works

When an inference request arrives at the Proxy, the Proxy "parks" the request and initiates a callback to the EPP via the `ext-proc` (External Processing) protocol. 

The EPP evaluates the request against the current state of the [InferencePool](../inferencepool.md)—considering factors like KV-cache locality, current load, and priority—and returns the address of the optimal model server pod back to the Proxy. The Proxy then forwards the original request to that specific destination.

This decoupled architecture allows llm-d to leverage the performance and reliability of production-grade proxies while providing a highly extensible framework for LLM-specific routing logic.



