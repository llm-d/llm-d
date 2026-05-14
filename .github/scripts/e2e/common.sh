#!/usr/bin/env bash

# Common E2E utilities for llm-d

setup_curl_pod() {
  # Delete any leftover pod with the same name (idempotent)
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" \
    --ignore-not-found >/dev/null 2>&1 || true

  echo "Creating persistent curl pod ${CURL_POD_NAME}..."
  kubectl run "$CURL_POD_NAME" \
    --namespace "$NAMESPACE" \
    --image=curlimages/curl \
    --restart=Never \
    -- sleep 3600 >/dev/null

  # Wait for the pod to be ready
  if ! kubectl wait --for=condition=Ready \
       pod/"$CURL_POD_NAME" -n "$NAMESPACE" \
       --timeout="${CURL_POD_TIMEOUT_SECONDS:-120}s"; then
    echo "Error: curl pod failed to become ready" >&2
    kubectl describe pod -n "$NAMESPACE" "$CURL_POD_NAME" >&2 2>/dev/null || true
    exit 1
  fi
  echo "Persistent curl pod is ready."
}

cleanup_curl_pod() {
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" \
    --ignore-not-found >/dev/null 2>&1 || true
}

run_curl() {
  CURL_OUTPUT=""
  CURL_EXIT=0
  CURL_OUTPUT=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- "$@" 2>&1) || CURL_EXIT=$?
}

discover_model() {
  local svc_host="$1"
  local max_retries="${MAX_RETRIES:-10}"
  local retry_delay="${RETRY_DELAY:-10}"
  local model_id=""

  echo "Attempting to auto-discover model ID from ${svc_host}/v1/models..."

  for attempt in $(seq 1 $max_retries); do
    echo "Attempt $attempt of $max_retries to discover model ID..."
    run_curl curl -sS --max-time 15 "http://${svc_host}/v1/models"
    response="$CURL_OUTPUT"
    ret="$CURL_EXIT"

    model_id=$(echo "$response" | grep -o '"id":"[^"]*"' | head -n 1 | cut -d '"' -f 4) || true

    if [[ -n "$model_id" ]]; then
      echo "Successfully discovered model ID: $model_id"
      echo "$model_id"
      return 0
    fi

    if echo "$response" | grep -q "404\|No healthy upstream\|no endpoints"; then
      echo "Gateway not ready yet (attempt $attempt): endpoints not available"
    elif [[ $ret -ne 0 ]]; then
      echo "Request failed (attempt $attempt, exit code $ret)"
    else
      echo "Empty or invalid response (attempt $attempt)"
    fi

    if [[ $attempt -lt $max_retries ]]; then
      echo "Waiting ${retry_delay}s before retry..."
      sleep $retry_delay
    fi
  done

  echo "Error: Failed to auto-discover model ID from gateway after $max_retries attempts." >&2
  return 1
}
