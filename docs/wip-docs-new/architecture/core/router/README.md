# llm-d Router

The **llm-d Router** is the intelligent entry point for inference requests in the llm-d stack. It provides sophisticated, LLM-aware load balancing, request queuing, and policy enforcement without reimplementing a full-featured network proxy.

## Composition

The llm-d Router is composed of two primary functional parts:

1.  **Proxy**: Any conformant industry-grade L7 proxy (typically [Envoy](proxy.md)). The proxy handles the data plane, including connection management, TLS termination, and request forwarding.
2.  **Endpoint Picker (EPP)**: A specialized service that the proxy consults for every request. The [EPP](epp/README.md) contains the routing "intelligence," using real-time signals from model servers to make optimal placement decisions.

## How it Works

When an inference request arrives at the Proxy, the Proxy "parks" the request and initiates a callback to the EPP via the `ext-proc` (External Processing) protocol. 

The EPP evaluates the request against the current state of the [InferencePool](../inferencepool.md)—considering factors like KV-cache locality, current load, and priority—and returns the address of the optimal model server pod back to the Proxy. The Proxy then forwards the original request to that specific destination.

This decoupled architecture allows llm-d to leverage the performance and reliability of production-grade proxies while providing a highly extensible framework for LLM-specific routing logic.

## Deep Dive

For more detailed information on the individual components of the llm-d Router, see:

- [**Proxy**](proxy.md): Learn about deployment modes (Standalone vs. Gateway Mode), request flow, and Gateway API integration.
- [**Endpoint Picker (EPP)**](epp/README.md): Explore the routing engine's architecture, plugin pipeline (Filters, Scorers, Pickers), and flow control mechanisms.

