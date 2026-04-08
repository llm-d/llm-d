# Endpoint Picker (EPP)

## Functionality

The EPP is the "brains" of an llm-d deployment. It is focused on two key objectives:

- **Intelligent scheduling** - selecting which **model server pod** within an InferencePool should process each inference request, using the internal state of model server pods (KV-cache utilization, prefix cache locality, request queue depth, and active request counts)

- **Fairness and priortiziaton** - selecting which **inference requests** should run at any given time, enabling consolidation of multiple workloads onto a single InferencePool

The EPP is an extensible component that integrates with the proxy layer via Envoy's [External Processing (ext-proc)](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) protocol.

When a request arrives at the proxy, the proxy calls the EPP to select a backend endpoint, and the EPP returns the optimal pod address according to the [Endpoint Picking Protocol](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/docs/proposals/004-endpoint-picker-protocol).

## Design

### Request Flow

The following diagram shows the end-to-end lifecycle of a request as it flows through the EPP plugin pipeline:

![Design](../../../../assets/epp-design.svg)

The steps are:

1. **Request arrival** -- An inference request arrives at the proxy (Gateway).
2. **ext-proc** -- The proxy invokes the EPP via the ext-proc protocol, passing the request headers and body to the EPP.
3. **Request handling** -- Parses the request (from e.g. OpenAI format, vllm gRPC) into the internal request data structure.
3. **Flow control** -- If (optionally) enabled, queues requests and prioritizes among different tenants and request priorities in saturation regimes.
4. **Scheduling** -- Filters, scores, and select optimal pod in the inference pool for a request.
7. **Response** -- The EPP returns the selected endpoint address to the proxy, which routes the request to that model server pod.

Asynchronously, the **Data layer* sets up watches on the Kubernetes API server for updates to relevant objects like InferencePools and pods for endpoint discovery. It is also responsible for model servers metrics probing, and maintaining an internal state—such as a prefix cache tree—to inform the request processing components, Flow Control and Scheduling.```

### Layers

The EPP is modular and pluggible, consisting of the following layers:

#### Ext-Proc Server

The Ext-Proc Server protocol is very well defined & specific, deviation could cause the EPP to become unusable or unstable. Extension is ill-advised. The Ext-Proc is simply the standard interface by which the Proxy talks to the EPP.

#### Data Layer (Extensible)

The **Data Layer** operates asynchronously, consuming and storing data from a variety of sources:
- Kube API Server about which pods are active in the InferencePool
- Model Servers about the current internal state (running requests, kv cache utilization)
- In-memory data structures, such as prefix cache trees for prefix-aware routing
- "Consultant" pods like latency predictor or the kv-indexer for advanced schemes

Other modules in the EPP consult the **Data Layer** during request processing.

#### Request Handler (Extensible)

The **Request Handler** is the first step of the request flow in the EPP. Its responsibility is to convert the user's request into the internal EPP data structure. The EPP provides some out-of-the-box Request Handlers for common protocols like the OpenAI `/v1/chat/completions`.

In additon, users can write a custom handler for their own protocol. The rest of the functionality in EPP is agnostic to the original request protocol, enabling easy adaptation of the EPP to new APIs.

See [Request Handling](request-handling.md) for more details.

#### Flow Control (Extensible)

XXX

See [Flow Control](flow-control.md) for more details.

#### Schedulger (Extensible)

XXX

See [Scheduling](scheduling.md) for more details.



