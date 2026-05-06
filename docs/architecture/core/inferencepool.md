# InferencePool

The `InferencePool` is the central resource that bridges the gap between the Gateway, the Endpoint Policy Provider (EPP), and the collection of model server instances. It serves as the source of truth for both endpoint discovery and service mesh/gateway integration.

## Functional Overview

The `InferencePool` performs two primary roles in the inference infrastructure:

1.  **Endpoint Discovery for the EPP:** It defines how the EPP should find and monitor the model server Pods that are eligible to serve requests.
2.  **Service Integration for the Gateway:** It provides the necessary metadata for the Gateway controller to locate the EPP and connect it to the proxy as an external processing (`ext-proc`) service.

## Architecture and Relations

The following diagram visualizes how the `InferencePool` resource is involved in the control path of both the EPP and Gateway Controller:

![InferencePool](../../assets/inferencepool.svg)

### 1. Endpoint Discovery (EPP Perspective)

The EPP uses the `InferencePool` to discover which pods it can pick from.

*   **Selector-based Discovery:** The `InferencePool` defines a `selector` (label matching). The EPP watches for Pods that match these labels within the same namespace.
*   **Dynamic Membership:** As model server Pods are scaled up or down, or as their readiness state changes, the EPP automatically updates its internal list of healthy candidates.
*   **Port Mapping:** The `targetPorts` in the `InferencePool` tell the EPP which ports on the discovered Pods are listening for inference traffic (e.g., port 8000 for vLLM).

### 2. Gateway Integration (Controller Perspective)

When an `InferencePool` is used as a backendRef in an `HTTPRoute`, the Gateway controller uses the resource to configure the underlying proxy.

*   **EPP Connectivity:** The `endpointPickerRef` (or `extensionRef`) in the `InferencePool` points to the EPP service. The Gateway controller uses this information to configure the proxy's `ext_proc` filter, ensuring that every request directed to the pool is first processed by the EPP.
*   **Routing Logic:** The proxy is configured to "park" the request and wait for the EPP's decision. The EPP then instructs the proxy—via the `ext_proc` protocol—on which specific Pod IP from the discovered pool should receive the request.
*   **Failure Handling:** The `failureMode` defined in the `InferencePool` (e.g., `FailOpen` or `FailClose`) tells the Gateway controller how to configure the proxy's behavior if the EPP becomes unresponsive.

## Key Relationships

*   **One-to-One Mapping:** Typically, one `InferencePool` corresponds to one logical deployment of a model (e.g., Gemma4) and is served by one EPP deployment.
*   **Decoupled Scaling:** The model servers can scale independently of the EPP. The `InferencePool` ensures the EPP is always aware of the current set of available endpoints.
*   **Namespace Scoped:** All discovery and references (Pods, EPP Service, and the InferencePool itself) are strictly contained within the same Kubernetes namespace to maintain security and isolation boundaries.
