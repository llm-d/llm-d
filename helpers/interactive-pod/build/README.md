# interactive pod

This directory contains an interactive pod. It has a version of guidellm built into it that supports stripping max_completion_tokens from its requests.

It has a `prepare-inference.sh` script to grab the gateway address and service endpoints.

It has `kubectl` baked into the image so you can see most if not all `llm-d` resources.

## Guidellm example

Sourcing the prepare inference will feed you all the values you need to bench from `guidellm`.

```bash
source prepare-inference.sh
guidellm benchmark \
  --target "${GATEWAY_SERVICE_ENDPOINT}" \
  --rate-type sweep \
  --max-seconds 30 \
  --model "${MODEL_NAME}" \
  --data "prompt_tokens=256,output_tokens=128"
```
