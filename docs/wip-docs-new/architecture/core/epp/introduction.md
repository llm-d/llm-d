# Endpoint Picker (EPP)

## Functionality

The EPP is the "brains" of an llm-d deployment. It is focused on two key objectives:

- **Intelligent scheduling** - selecting which **model server pod** within an InferencePool should process each inference request, using the internal state of model server pods (KV-cache utilization, prefix cache locality, request queue depth, and active request counts)

- **Fairness and priortiziaton** - selecting which **inference requests** should run at any given time, enabling consolidation of multiple workloads onto a single InferencePool

The EPP is a pluggible, extensible component that integrates with the proxy layer via Envoy's [External Processing (ext-proc)](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) protocol. When a request arrives at the proxy, the proxy calls the EPP to select a backend endpoint, and the EPP returns the optimal pod address.

## Design

### Lifecycle of a Request

The following diagram shows the end-to-end lifecycle of a request as it flows through the EPP plugin pipeline:

![Design](../../../../assets/epp-design.svg)

The steps are:

1. **Request arrival** -- An inference reques arrives at the proxy (Gateway).
2. **ext-proc** -- The proxy invokes the EPP via the ext-proc protocol, passing the request headers and body to the EPP.
3. **Request handling** -- Processes the request (in e.g. OpenAI format) into the internal data structure.
3. **Flow control** -- If (optionally) enabled, queues requests and prioritizes among different tenants and request priorities in saturation regimes.
4. **Scheduling** -- Filters, scores, and select optimal pod in the inference pool for a request.
7. **Response** -- The EPP returns the selected endpoint address to the proxy, which routes the request to that model server pod.

Asynchronously, the **Data layer** consults the Kube API server for service discover, probes the model servers for their metrics, and maintains internal state (e.g. a prefix cache tree) and is consulted by the Flow Control and Scheduling modules.

Each of these steps is pluggible and configurable, enabling customization.




