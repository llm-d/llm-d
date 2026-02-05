#!/usr/bin/env bash
GATEWAY_ADDRESS=$(kubectl get gateway -o jsonpath='{.items[0].status.addresses[0].value}')
MODEL_NAME=$(curl "http://${GATEWAY_ADDRESS}/v1/models" | jq '.data[0].id' | cut -d "\"" -f 2)

guidellm benchmark \
      --target "http://${GATEWAY_ADDRESS}" \
      --rate-type sweep \
      --max-seconds 30 \
      --model "${MODEL_NAME}" \
      --data "prompt_tokens=256,output_tokens=128"
