# EPP HTTP APIs Reference

This document lists the HTTP APIs the [Endpoint Picker (EPP)](../architecture/core/router/epp) supports for inference traffic. Depending on the API, the EPP may parse fields from the request body to do prefix-cache aware routing, and plugin decisions.

## Supported HTTP APIs

| Endpoint | Source | Supported |
| --- | --- | --- |
| `/v1/completions` | OpenAI Completions API | ✅ |
| `/v1/chat/completions` | OpenAI Chat Completions API | ✅ |
| `/v1/responses` | OpenAI Responses API | ✅ |
| `/v1/embeddings` | OpenAI Embeddings API | ✅ |
| `/v1/messages` | Anthropic Messages API | ✅ |
| `/inference/v1/generate` | vLLM Generate API | ✅ |

---

## Request Examples

The examples below parameterize the model as `${MODEL_NAME}` and the proxy endpoint as `${IP}`. Set `${MODEL_NAME}` to [`Qwen/Qwen3-VL-32B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct) from the [multimodal optimized-baseline guide](../../guides/multimodal-serving/optimized-baseline/README.md), and set `${IP}` to the proxy endpoint IP retrieved per that guide's verification steps.

```bash
export MODEL_NAME=Qwen/Qwen3-VL-32B-Instruct
```

The `/v1/embeddings` section overrides `${MODEL_NAME}` since chat/instruct models do not expose that route.

### OpenAI `/v1/completions`

Request:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "prompt": "Hello",
        "max_tokens": 10
    }' | jq
```

Response:

```json
{
  "id": "cmpl-abc123",
  "object": "text_completion",
  "created": 1781036021,
  "model": "Qwen/Qwen3-VL-32B-Instruct",
  "choices": [
    {
      "index": 0,
      "text": "! I am trying to write a story, and",
      "logprobs": null,
      "finish_reason": "length",
      "stop_reason": null
    }
  ],
  "system_fingerprint": "vllm-0.21.0-tp2-5054d0df",
  "usage": {
    "prompt_tokens": 1,
    "total_tokens": 11,
    "completion_tokens": 10
  }
}
```

Streaming request (set `stream: true`; the response is server-sent events, so drop `jq` and use `curl -N` to flush chunks as they arrive):

```bash
curl -N -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "prompt": "Hello",
        "max_tokens": 10,
        "stream": true
    }'
```

### OpenAI `/v1/chat/completions`

Request:

```bash
curl -X POST http://${IP}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": "https://picsum.photos/640/360"}}
                ]
            }
        ],
        "max_tokens": 10
    }' | jq
```

Streaming request:

```bash
curl -N -X POST http://${IP}/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": "https://picsum.photos/640/360"}}
                ]
            }
        ],
        "max_tokens": 10,
        "stream": true
    }'
```

<details>
<summary>Streaming response (SSE)</summary>

```
data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":"!","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" I","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":"'m","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" a","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" student","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" of","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" the","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":" ","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":"1","logprobs":null,"finish_reason":null,"stop_reason":null}]}

data: {"id":"cmpl-abc124","object":"text_completion","created":1781036045,"model":"Qwen/Qwen3-VL-32B-Instruct","choices":[{"index":0,"text":"0","logprobs":null,"finish_reason":"length","stop_reason":null}],"system_fingerprint":"vllm-0.21.0-tp2-5054d0df"}

data: [DONE]
```

</details>

### OpenAI `/v1/responses`

Request:

```bash
curl -X POST http://${IP}/v1/responses \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "input": "Hello",
        "max_output_tokens": 10
    }' | jq
```

Response:

```json
{
  "id": "resp_abc127",
  "created_at": 1781036107,
  "incomplete_details": {"reason": "max_output_tokens"},
  "model": "Qwen/Qwen3-VL-32B-Instruct",
  "object": "response",
  "output": [
    {
      "id": "msg_abc128",
      "type": "message",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "Hello! How can I help you today?",
          "annotations": []
        }
      ]
    }
  ],
  "status": "incomplete",
  "max_output_tokens": 10,
  "usage": {
    "input_tokens": 9,
    "output_tokens": 10,
    "total_tokens": 19
  }
}
```

### OpenAI `/v1/embeddings`

This endpoint requires an embedding model deployment (for example `Qwen/Qwen3-Embedding-0.6B`). Chat/instruct models do not expose this route.

Request:

```bash
export MODEL_NAME=Qwen/Qwen3-Embedding-0.6B
curl -X POST http://${IP}/v1/embeddings \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "input": "Hello"
    }' | jq
```

Response (embedding vector truncated for readability):

```json
{
  "model": "Qwen/Qwen3-Embedding-0.6B",
  "object": "list",
  "data": [
    {
      "index": 0,
      "object": "embedding",
      "embedding": [-0.01350, -0.02152, -0.01368, -0.03032, 0.00941, "..."]
    }
  ],
  "usage": {
    "prompt_tokens": 2,
    "total_tokens": 2,
    "completion_tokens": 0
  }
}
```

### Anthropic `/v1/messages`

Request:

```bash
curl -X POST http://${IP}/v1/messages \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image", "source": {"type": "url", "url": "https://picsum.photos/640/360"}}
                ]
            }
        ],
        "max_tokens": 10
    }' | jq
```

Response:

```json
{
  "id": "chatcmpl-abc125",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "This image is a close-up, shallow-focus photograph"
    }
  ],
  "model": "Qwen/Qwen3-VL-32B-Instruct",
  "stop_reason": "max_tokens",
  "usage": {
    "input_tokens": 234,
    "output_tokens": 10
  }
}
```

Streaming request:

```bash
curl -N -X POST http://${IP}/v1/messages \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image", "source": {"type": "url", "url": "https://picsum.photos/640/360"}}
                ]
            }
        ],
        "max_tokens": 10,
        "stream": true
    }'
```

<details>
<summary>Streaming response (SSE)</summary>

```
event: message_start
data: {"type":"message_start","message":{"id":"chatcmpl-abc126","content":[],"model":"Qwen/Qwen3-VL-32B-Instruct","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":234,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","content_block":{"type":"text","text":""},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"This"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" image"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" captures"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" a"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" serene"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" and"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" atmospheric"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" urban"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" landscape"},"index":0}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" at"},"index":0}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"input_tokens":234,"output_tokens":10}}

event: message_stop
data: {"type":"message_stop"}
```

</details>


### vLLM `/inference/v1/generate`

This endpoint requires the model server to be vLLM. Sampling controls must be nested inside a `sampling_params` object rather than placed at the top level.

Request:

```bash
curl -X POST http://${IP}/inference/v1/generate \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "'"${MODEL_NAME}"'",
        "token_ids": [9906],
        "sampling_params": {"max_tokens": 10}
    }' | jq
```

Response:

```json
{
  "request_id": "abc129",
  "choices": [
    {
      "index": 0,
      "logprobs": null,
      "finish_reason": "length",
      "token_ids": [17993, 1894, 7332, 198, 286, 2415, 1140, 259, 4580, 892]
    }
  ]
}
```
