#!/usr/bin/env bash
# Shared functions for E2E validation scripts.
#
# Callers must set NAMESPACE and CURL_POD_NAME before sourcing.
# Callers own trap registration (call cleanup_curl_pod in their own trap).

CURL_POD_TIMEOUT_SECONDS="${CURL_POD_TIMEOUT_SECONDS:-120}"

setup_curl_pod() {
  kubectl delete pod -n "$NAMESPACE" "$CURL_POD_NAME" \
    --ignore-not-found >/dev/null 2>&1 || true

  echo "Creating persistent curl pod ${CURL_POD_NAME}..."
  kubectl run "$CURL_POD_NAME" \
    --namespace "$NAMESPACE" \
    --image=curlimages/curl \
    --restart=Never \
    -- sleep 3600 >/dev/null

  if ! kubectl wait --for=condition=Ready \
       pod/"$CURL_POD_NAME" -n "$NAMESPACE" \
       --timeout="${CURL_POD_TIMEOUT_SECONDS}s"; then
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

# Usage: run_curl <args...>
# Sets:  CURL_OUTPUT  — captured stdout
#        CURL_EXIT    — curl exit code (0 = success)
run_curl() {
  CURL_OUTPUT=""
  CURL_EXIT=0
  CURL_OUTPUT=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- "$@" 2>&1) || CURL_EXIT=$?
}

# Discover the EPP service ClusterIP for standalone-mode guides.
# Sets: HOST, SVC_HOST, EPP_HOST, EPP_METRICS_URL
# Requires: NAMESPACE, EPP_METRICS_PORT, EPP_HOST_OVERRIDE (optional)
discover_epp_service() {
  HOST="${GATEWAY_HOST:-}"
  if [[ -z "$HOST" ]]; then
    local epp_svc_name
    epp_svc_name=$(kubectl get svc -n "$NAMESPACE" \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null \
        | tr ' ' '\n' | grep -E -- '-epp$' | head -1 || true)
    if [[ -n "$epp_svc_name" ]]; then
      HOST=$(kubectl get svc "$epp_svc_name" -n "$NAMESPACE" \
        -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
    fi
  fi
  if [[ -z "$HOST" ]]; then
    echo "Error: could not discover EPP service in namespace '$NAMESPACE'." >&2
    echo "       Set GATEWAY_HOST env var to the EPP service name or ClusterIP." >&2
    exit 1
  fi
  SVC_HOST="${HOST}:80"
  EPP_HOST="${EPP_HOST_OVERRIDE:-$HOST}"
  EPP_METRICS_URL="http://${EPP_HOST}:${EPP_METRICS_PORT}/metrics"
}

# Auto-discover model ID with retries.
# Sets: MODEL_ID
# Requires: SVC_HOST, CURL_POD_NAME, NAMESPACE
# Respects: CLI_MODEL_ID (takes priority), MODEL_ID env var
discover_model() {
  if [[ -n "${CLI_MODEL_ID:-}" ]]; then
    MODEL_ID="$CLI_MODEL_ID"
    return
  fi
  if [[ -n "${MODEL_ID-}" ]]; then
    return
  fi
  echo "Attempting to auto-discover model ID from ${SVC_HOST}/v1/models..."

  local max_retries=10 retry_delay=10 attempt response ret
  MODEL_ID=""

  for attempt in $(seq 1 $max_retries); do
    echo "Attempt $attempt of $max_retries to discover model ID..."
    run_curl curl -sS --max-time 15 "http://${SVC_HOST}/v1/models"
    response="$CURL_OUTPUT"
    ret="$CURL_EXIT"

    MODEL_ID=$(echo "$response" | grep -o '"id":"[^"]*"' | head -n 1 | cut -d '"' -f 4) || true

    if [[ -n "$MODEL_ID" ]]; then
      echo "Successfully discovered model ID: $MODEL_ID"
      return
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

  echo "Error: Failed to auto-discover model ID after $max_retries attempts." >&2
  echo "Last response: $response" >&2
  echo "You can specify one using the -m flag or the MODEL_ID environment variable." >&2
  exit 1
}
