#!/usr/bin/env bash
set -Eeuo pipefail

GATEWAY_ADDRESS=$(kubectl get gateway -n "${NAMESPACE:?NAMESPACE is required}" -o jsonpath='{.items[0].status.addresses[0].value}')
if [[ -z "${GATEWAY_ADDRESS}" ]]; then
  echo "Error: Gateway address is empty" >&2
  exit 1
fi

MODEL_NAME=$(curl "http://${GATEWAY_ADDRESS}/v1/models" | jq '.data[0].id' | cut -d "\"" -f 2)

guidellm benchmark \
      --target "http://${GATEWAY_ADDRESS}" \
      --rate-type sweep \
      --max-seconds 30 \
      --model "${MODEL_NAME}" \
      --data "prompt_tokens=256,output_tokens=128"
