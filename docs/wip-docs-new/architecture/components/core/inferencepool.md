# InferencePool

The InferencePool is a Kubernetes custom resource that defines a group of Model Server Pods dedicated to serving AI models.

## Functionality

An [InferencePool](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/) is a Kubernetes custom resource from the [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) project. It provides a centralized point of administrative configuration for Platform Admins by abstracting the management of AI model serving resources.

All Pods within an InferencePool share the same:

- **Compute configuration** (CPU, memory, GPU resources)
- **Accelerator type** (e.g., NVIDIA A100, H100, CPU)
- **Model server** (vLLM or SGLang)

In the llm-d architecture, the InferencePool sits between the EPP (Endpoint Picker Pod) and the Model Servers:

```
External Traffic
    |
[ Proxy (Gateway API / Standalone Envoy) ] <-> [ EPP (Endpoint Picker Pod) ]
    |
[ InferencePool ]
    |
[ Model Servers (vLLM / SGLang) ]
```

The EPP uses the InferencePool to discover available Model Server endpoints and intelligently route inference requests to the optimal replica based on metrics like KV-cache utilization, queue depth, and prefix cache hits.

## Design

### InferencePool Spec

The InferencePool custom resource has three core fields:

#### `selector`

A set of label key-value pairs used to identify which Pods belong to the pool. Labels must exactly match the labels applied to your Model Server Pods. Model Servers join a pool automatically when their labels match -- no explicit registration is required.

#### `targetPorts`

The port number(s) the gateway uses to route traffic to Model Server Pods within the pool. For standard deployments, a single port (typically `8000`) is sufficient. For advanced use cases like Data Parallelism (DP)-aware routing, multiple ports can be specified to address individual DP ranks within a Pod.

#### `extensionRef`

A reference to the Endpoint Picker extension service that monitors metrics and provides routing decisions. This is managed by the Helm chart and includes the service name, port number, and failure mode.

A raw InferencePool resource looks like this:

```yaml
apiVersion: inference.networking.k8s.io/v1
kind: InferencePool
metadata:
  name: llm-d-infpool
spec:
  targetPorts:
    - number: 8000
  selector:
    llm-d.ai/inference-serving: "true"
  extensionRef:
    name: llm-d-infpool-epp
    port: 9002
    failureMode: FailOpen
```

> In llm-d, the InferencePool resource and its associated EPP are deployed together via the upstream `inferencepool` Helm chart. You configure the pool and the EPP through Helm values rather than writing the raw CR directly.

### How Model Servers Join a Pool

Model Servers are discovered dynamically via Kubernetes label selectors. To add a Model Server to an InferencePool, apply the labels specified in `modelServers.matchLabels` to the Model Server's Pod template. At minimum, set:

```yaml
labels:
  llm-d.ai/inference-serving: "true"
```

No explicit registration or enrollment is required. Once the labels match, the Model Server Pods automatically appear as endpoints in the InferencePool and the EPP begins routing traffic to them.

Model Server Pods must support the [model server protocol](https://gateway-api-inference-extension.sigs.k8s.io/) defined by the Gateway API Inference Extension project so the EPP can collect metrics (KV-cache utilization, queue length, active LoRA adapters) to make intelligent routing decisions.

### Gateway API Integration

When using Gateway API, the InferencePool is referenced as a backend in an `HTTPRoute`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-d-route
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: llm-d-inference-gateway
  rules:
    - backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: llm-d-infpool
          port: 8000
      matches:
        - path:
            type: PathPrefix
            value: /
```

This routes all incoming traffic through the Gateway to the InferencePool, where the EPP selects the optimal Model Server endpoint for each request.

### Deployment

Deploy the InferencePool using the upstream Helm chart, selecting the appropriate provider for your environment.

<!-- TABS:START -->

<!-- TAB:GKE:default -->
#### GKE

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=gke" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TAB:Istio -->
#### Istio

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=istio" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TAB:Agentgateway -->
#### Agentgateway

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=none" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TABS:END -->

> Prefer `agentgateway` for new self-installed inference deployments. The current Gateway API Inference Extension chart uses `provider.name=none` for both `agentgateway` and the deprecated `kgateway` migration path.

After installation, verify the resources:

```bash
kubectl get inferencepool -n ${NAMESPACE}
kubectl get pods -l app.kubernetes.io/instance=llm-d-infpool -n ${NAMESPACE}
```

## Configuration

### Helm Values

The InferencePool is deployed using the upstream Helm chart from the Gateway API Inference Extension project:

```
oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

Configuration is split into two sections in the Helm values file: `inferencePool` (the pool itself) and `inferenceExtension` (the EPP that ships alongside it).

#### `inferencePool` Section

| Field | Description | Example |
|---|---|---|
| `targetPorts` | List of port numbers to route traffic to on Model Server Pods | `[{number: 8000}]` |
| `modelServerType` | Type of model server (`vllm` or `sglm`) | `vllm` |
| `modelServers.matchLabels` | Kubernetes label selector for discovering Model Server Pods | `{llm-d.ai/inference-serving: "true"}` |

#### `inferenceExtension` Section

| Field | Description | Example |
|---|---|---|
| `replicas` | Number of EPP replicas | `1` |
| `image` | Container image for the EPP | `ghcr.io/llm-d/llm-d-inference-scheduler:v0.7.0` |
| `extProcPort` | Port the EPP listens on for ext-proc traffic from the proxy | `9002` |
| `pluginsConfigFile` | Filename for the scheduling plugin configuration | `"custom-plugins.yaml"` |
| `pluginsCustomConfig` | Inline scheduling plugin configuration (see [EPP](epp.md)) | See examples below |
| `tracing.enabled` | Enable OpenTelemetry distributed tracing | `false` |
| `monitoring.prometheus.enabled` | Enable Prometheus metrics scraping | `true` |

## Examples

### Minimal Deployment

A minimal values file for a standard deployment:

```yaml
inferencePool:
  modelServers:
    matchLabels:
      llm-d.ai/inference-serving: "true"
inferenceExtension:
  tracing:
    enabled: false
  monitoring:
    prometheus:
      enable: true
```

### Prefill/Decode Disaggregation

For P/D disaggregation, the EPP is configured with scheduling profiles that route prefill and decode phases to different endpoints:

```yaml
inferencePool:
  targetPorts:
    - number: 8000
  modelServerType: vllm
  modelServers:
    matchLabels:
      llm-d.ai/inference-serving: "true"
      llm-d.ai/guide: "pd-disaggregation"
inferenceExtension:
  replicas: 1
  image:
    name: llm-d-inference-scheduler
    hub: ghcr.io/llm-d
    tag: v0.7.0
  extProcPort: 9002
  pluginsConfigFile: "pd-config.yaml"
  pluginsCustomConfig:
    pd-config.yaml: |
      apiVersion: inference.networking.x-k8s.io/v1alpha1
      kind: EndpointPickerConfig
      plugins:
      - type: prefill-header-handler
      - type: prefix-cache-scorer
      - type: queue-scorer
      - type: prefill-filter
      - type: decode-filter
      - type: max-score-picker
      - type: prefix-based-pd-decider
        parameters:
          nonCachedTokens: 16
      - type: pd-profile-handler
        parameters:
          primaryPort: 0
          deciderPluginName: prefix-based-pd-decider
      schedulingProfiles:
      - name: prefill
        plugins:
        - pluginRef: prefill-filter
        - pluginRef: max-score-picker
        - pluginRef: prefix-cache-scorer
          weight: 2
        - pluginRef: queue-scorer
          weight: 1
      - name: decode
        plugins:
        - pluginRef: decode-filter
        - pluginRef: max-score-picker
        - pluginRef: prefix-cache-scorer
          weight: 2
        - pluginRef: queue-scorer
          weight: 1
  monitoring:
    prometheus:
      enabled: true
```

### Multi-Port (DP-Aware) Routing

For Data Parallelism-aware routing, multiple `targetPorts` are specified so the EPP can schedule requests directly to specific DP ranks within a Pod:

```yaml
inferencePool:
  targetPorts:
    - number: 8000
    - number: 8001
    - number: 8002
    - number: 8003
    - number: 8004
    - number: 8005
    - number: 8006
    - number: 8007
  modelServers:
    matchLabels:
      llm-d.ai/inference-serving: "true"
      llm-d.ai/guide: "wide-ep-lws"
```

## Further Reading

- [Upstream InferencePool API docs](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/)
- [EPP (Endpoint Picker Pod)](epp.md) -- scheduling plugins and profiles
- [Proxy](proxy.md) -- Gateway API and Standalone proxy configuration
- [Model Servers](model-servers.md) -- vLLM and SGLang configuration
