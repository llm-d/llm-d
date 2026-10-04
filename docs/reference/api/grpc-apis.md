# EPP gRPC APIs Reference

This document lists the gRPC APIs the [Endpoint Picker (EPP)](../architecture/core/router/epp) supports for inference traffic. gRPC requests flow through the gateway as HTTP/2 (H2C) traffic, and the EPP decodes the gRPC frames and protobuf payloads to do prefix-cache aware routing, plugin decisions, and response usage tracking.

Unlike the HTTP APIs, gRPC parsing is not enabled by default: the matching parser plugin must be configured in the [EndpointPickerConfig](endpointpickerconfig.md).

## Supported gRPC APIs

| gRPC Method | Source | Parser Plugin | Supported |
| --- | --- | --- | --- |
| `vllm.grpc.engine.VllmEngine/Generate` | vLLM gRPC engine API | `vllmgrpc-parser` | ✅ |
| `vllm.grpc.engine.VllmEngine/Embed` | vLLM gRPC engine API | `vllmgrpc-parser` | ✅ |

The gRPC API is currently token-out only for `Generate`: responses carry token IDs (`chunk.token_ids`, `complete.output_ids`) rather than decoded text, and clients are responsible for detokenization.

## Parser Configuration

Parsers are configured via the `requestHandler.parsers` section of the EndpointPickerConfig. Instantiate the parser plugin in `plugins`, then reference it by name:

```yaml
apiVersion: llm-d.ai/v1alpha1
kind: EndpointPickerConfig
plugins:
- name: maxScore
  type: max-score-picker
- name: vllmgrpcParser
  type: vllmgrpc-parser
schedulingProfiles:
  # ... omitted for brevity ...
requestHandler:
  parsers:
  - pluginRef: vllmgrpcParser
```

## InferencePool Configuration

gRPC requires HTTP/2 end to end. For the gateway to connect to the model server pods with HTTP/2 cleartext (h2c), the `InferencePool` must set `appProtocol: kubernetes.io/h2c`.

```yaml
apiVersion: inference.networking.k8s.io/v1
kind: InferencePool
metadata:
  name: vllm-grpc-qwen3-32b
spec:
  targetPorts:
  - number: 8000
  appProtocol: kubernetes.io/h2c
  selector:
    matchLabels:
      app: vllm-grpc-qwen3-32b
  endpointPickerRef:
    name: vllm-grpc-qwen3-32b-epp
    port:
      number: 9002
```

When deploying with the [llm-d-router Helm charts](https://github.com/llm-d/llm-d-router/tree/main/config/charts), setting `router.modelServers.protocol=grpc` configures this automatically.

---

## Request Examples

The examples below use [grpcurl](https://github.com/fullstorydev/grpcurl) with the proxy endpoint as `${IP}`, set per the relevant guide's verification steps. They require the [`vllm_engine.proto`](https://github.com/llm-d/llm-d-router/blob/main/pkg/epp/framework/plugins/requesthandling/parsers/vllmgrpc/api/proto/vllm_engine.proto) definition, and a model server that exposes the vLLM gRPC engine API.

### vLLM `VllmEngine/Generate`

Request (text input; alternatively pass pre-tokenized input via the `tokenized` field):

```bash
grpcurl -plaintext -proto vllm_engine.proto \
    -d '{
        "request_id": "req-1",
        "text": "Hello",
        "sampling_params": {"max_tokens": 10}
    }' \
    ${IP}:80 vllm.grpc.engine.VllmEngine/Generate
```

Response:

```json
{
  "complete": {
    "outputIds": [17993, 1894, 7332, 198, 286, 2415, 1140, 259, 4580, 892],
    "finishReason": "length",
    "promptTokens": 1,
    "completionTokens": 10
  }
}
```

Streaming request (set `"stream": true`; the server returns a stream of `GenerateResponse` messages with incremental `chunk` payloads followed by a final `complete` payload):

```bash
grpcurl -plaintext -proto vllm_engine.proto \
    -d '{
        "request_id": "req-2",
        "text": "Hello",
        "sampling_params": {"max_tokens": 10},
        "stream": true
    }' \
    ${IP}:80 vllm.grpc.engine.VllmEngine/Generate
```

<details>
<summary>Streaming response</summary>

```text
Response contents:
{
  "chunk": {
    "tokenIds": [
      883336980
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "chunk": {
    "tokenIds": [
      186949092
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "chunk": {
    "tokenIds": [
      446163293
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "chunk": {
    "tokenIds": [
      186949092
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "chunk": {
    "tokenIds": [
      3509523577
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "chunk": {
    "tokenIds": [
      1690122482
    ],
    "promptTokens": 10,
    "completionTokens": 1
  }
}

Response contents:
{
  "complete": {
    "finishReason": "stop",
    "promptTokens": 10
  }
}
```

</details>

### vLLM `VllmEngine/Embed`

This method requires pre-tokenized input and an embedding model deployment.

Request:

```bash
grpcurl -plaintext -proto vllm_engine.proto \
    -d '{
        "request_id": "req-3",
        "tokenized": {"original_text": "Hello", "input_ids": [9906]}
    }' \
    ${IP}:80 vllm.grpc.engine.VllmEngine/Embed
```

Response (embedding vector truncated for readability):

```json
{
  "embedding": [-0.01350, -0.02152, -0.01368, "..."],
  "promptTokens": 1,
  "embeddingDim": 1024
}
```

---

## HTTP Headers

The [EPP HTTP headers](epp-http-headers.md) (request classification, flow control, and SLO headers such as `x-llm-d-inference-objective` and `x-llm-d-inference-fairness-id`) work for gRPC requests exactly as they do for HTTP.

Specify them as gRPC metadata on the call. With grpcurl, use `-H`:

```bash
grpcurl -plaintext -proto vllm_engine.proto \
    -H 'x-llm-d-inference-objective: my-objective' \
    -H 'x-llm-d-inference-fairness-id: tenant-a' \
    -d '{
        "request_id": "req-4",
        "text": "Hello",
        "sampling_params": {"max_tokens": 10}
    }' \
    ${IP}:80 vllm.grpc.engine.VllmEngine/Generate
```

In a Go client, attach the metadata to the outgoing context:

```go
ctx = metadata.AppendToOutgoingContext(ctx,
    "x-llm-d-inference-objective", "my-objective",
    "x-llm-d-inference-fairness-id", "tenant-a")
resp, err := client.Generate(ctx, req)
```

In Python, pass `metadata` on the call:

```python
stub.Generate(request, metadata=(
    ("x-llm-d-inference-objective", "my-objective"),
    ("x-llm-d-inference-fairness-id", "tenant-a"),
))
```
