#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate.sh — CI e2e Gateway smoke-test (chat + completion, 10 iterations)
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE   Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID        Model to query. If unset, discovers the first available model.
  -v, --verbose               Echo kubectl/curl commands before running
  -h, --help                  Show this help and exit
EOF
  exit 0
}

# ── Defaults ────────────────────────────────────────────────────────────────
NAMESPACE="llm-d"
CLI_MODEL_ID=""
VERBOSE=false

# ── Flag parsing ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -m|--model)     CLI_MODEL_ID="$2"; shift 2 ;;
    -v|--verbose)   VERBOSE=true; shift ;;
    -h|--help)      show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

if [[ "${VERBOSE}" == "true" ]]; then
  set -x
fi

# ── Helper for unique pod suffix ────────────────────────────────────────────
gen_id() { echo $(( RANDOM % 10000 + 1 )); }

# ── Reliable curl-pod runner ───────────────────────────────────────────────
# Replaces `kubectl run --rm -i` which has a race condition: the container can
# finish before kubectl establishes the attach, losing all output.  The pod is
# also auto-deleted (--rm), so there is no way to recover the response.
#
# This helper uses create → wait → logs → delete, which is deterministic.
#
# Usage: run_curl_pod <pod-name> <args…>
# Sets:  CURL_OUTPUT  — captured stdout from the pod
#        CURL_EXIT    — container exit code (0 = success)
CURL_POD_TIMEOUT_SECONDS="${CURL_POD_TIMEOUT_SECONDS:-300}"  # max time to wait for a curl pod to complete (env-configurable)

run_curl_pod() {
  local pod_name="$1"; shift
  CURL_OUTPUT=""
  CURL_EXIT=0

  # Create the pod (returns immediately; pod runs in the background).
  # Allow stderr through so RBAC / quota / image-pull errors are visible in CI logs.
  kubectl run "$pod_name" \
    --namespace "$NAMESPACE" \
    --image=curlimages/curl \
    --restart=Never \
    -- "$@" >/dev/null

  # Poll until the pod reaches a terminal phase (Succeeded / Failed)
  local deadline=$((SECONDS + CURL_POD_TIMEOUT_SECONDS))
  local phase=""
  while [[ $SECONDS -lt $deadline ]]; do
    phase=$(kubectl get pod -n "$NAMESPACE" "$pod_name" \
      -o jsonpath='{.status.phase}' 2>/dev/null) || true
    case "$phase" in
      Succeeded|Failed) break ;;
      *) sleep 2 ;;
    esac
  done

  # Detect timeout: pod never reached a terminal phase
  if [[ "$phase" != "Succeeded" && "$phase" != "Failed" ]]; then
    echo "Error: curl pod $pod_name timed out after ${CURL_POD_TIMEOUT_SECONDS}s (last phase: ${phase:-Unknown})" >&2
    kubectl describe pod -n "$NAMESPACE" "$pod_name" >&2 2>/dev/null || true
    CURL_OUTPUT=""
    CURL_EXIT=1
    kubectl delete pod -n "$NAMESPACE" "$pod_name" \
      --ignore-not-found >/dev/null 2>&1 || true
    return
  fi

  # Capture logs. If none are available (e.g. pod never started), fall back to
  # pod description so CI logs contain something actionable.
  CURL_OUTPUT=$(kubectl logs -n "$NAMESPACE" "$pod_name" 2>/dev/null) || true
  if [[ -z "$CURL_OUTPUT" ]]; then
    echo "Warning: no logs from curl pod $pod_name — dumping pod description:" >&2
    kubectl describe pod -n "$NAMESPACE" "$pod_name" >&2 2>/dev/null || true
  fi

  # Retrieve the container exit code
  CURL_EXIT=$(kubectl get pod -n "$NAMESPACE" "$pod_name" \
    -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}' \
    2>/dev/null) || true
  CURL_EXIT="${CURL_EXIT:-1}"

  # Clean up
  kubectl delete pod -n "$NAMESPACE" "$pod_name" \
    --ignore-not-found >/dev/null 2>&1 || true
}

# ── Discover Gateway address ────────────────────────────────────────────────
HOST="${GATEWAY_HOST:-$(kubectl get gateway -n "$NAMESPACE" \
          -o jsonpath='{.items[0].status.addresses[0].value}' 2>/dev/null || true)}"
if [[ -z "$HOST" ]]; then
  echo "Error: could not discover a Gateway address in namespace '$NAMESPACE'." >&2
  exit 1
fi
PORT=80
SVC_HOST="${HOST}:${PORT}"

# ── Determine MODEL_ID ──────────────────────────────────────────────────────
# Priority: command-line > env var > auto-discovery
if [[ -n "$CLI_MODEL_ID" ]]; then
  MODEL_ID="$CLI_MODEL_ID"
elif [[ -n "${MODEL_ID-}" ]]; then
  MODEL_ID="$MODEL_ID"
else
  echo "Attempting to auto-discover model ID from ${SVC_HOST}/v1/models..."

  # Retry logic: wait for EPP to discover backends and gateway to be fully ready
  MAX_RETRIES=10
  RETRY_DELAY=10
  MODEL_ID=""

  for attempt in $(seq 1 $MAX_RETRIES); do
    echo "Attempt $attempt of $MAX_RETRIES to discover model ID..."
    ID=$(gen_id)
    run_curl_pod "curl-discover-${ID}" \
      curl -sS --max-time 15 "http://${SVC_HOST}/v1/models"
    response="$CURL_OUTPUT"
    ret="$CURL_EXIT"

    # Try to extract model ID from response
    MODEL_ID=$(echo "$response" | grep -o '"id":"[^"]*"' | head -n 1 | cut -d '"' -f 4) || true

    if [[ -n "$MODEL_ID" ]]; then
      echo "Successfully discovered model ID: $MODEL_ID"
      break
    fi

    # Check if we got a specific error
    if echo "$response" | grep -q "404\|No healthy upstream\|no endpoints"; then
      echo "Gateway not ready yet (attempt $attempt): endpoints not available"
    elif [[ $ret -ne 0 ]]; then
      echo "Request failed (attempt $attempt, exit code $ret)"
    else
      echo "Empty or invalid response (attempt $attempt)"
    fi

    if [[ $attempt -lt $MAX_RETRIES ]]; then
      echo "Waiting ${RETRY_DELAY}s before retry..."
      sleep $RETRY_DELAY
    fi
  done

  if [[ -z "$MODEL_ID" ]]; then
    echo "Error: Failed to auto-discover model ID from gateway after $MAX_RETRIES attempts." >&2
    echo "Last response: $response" >&2
    echo "You can specify one using the -m flag or the MODEL_ID environment variable." >&2
    exit 1
  fi
fi

echo "Namespace: $NAMESPACE"
echo "Inference Gateway:   ${SVC_HOST}"
echo "Model ID:  $MODEL_ID"
echo

# ── Main test loop (10 iterations) ──────────────────────────────────────────
for i in {1..10}; do
  echo "=== Iteration $i of 10 ==="
  failed=false

  # 1) POST /v1/chat/completions
  echo "1) POST /v1/chat/completions at ${SVC_HOST}"
  chat_payload='{
    "model":"'"$MODEL_ID"'",
    "messages":[{"role":"user","content":"Hello!  Who are you?"}]
  }'
  ID=$(gen_id)
  if $VERBOSE; then cat <<CMD
  - Running: run_curl_pod curl-${ID} (POST /v1/chat/completions)
CMD
  fi
  run_curl_pod "curl-$ID" \
    sh -c "curl -sS -X POST 'http://${SVC_HOST}/v1/chat/completions' \
         -H 'accept: application/json' \
         -H 'Content-Type: application/json' \
         -d '$chat_payload'"
  output="$CURL_OUTPUT"
  ret="$CURL_EXIT"
  echo "$output"
  [[ $ret -ne 0 || "$output" != *'{'* ]] && {
    echo "Error: POST /v1/chat/completions failed (exit $ret or no JSON)" >&2; failed=true; }
  echo

  # 2) POST /v1/completions
  echo "2) POST /v1/completions at ${SVC_HOST}"
  payload='{
    "model":"'"$MODEL_ID"'",
    "prompt":"You are a helpful AI assistant."
  }'
  ID=$(gen_id)
  if $VERBOSE; then cat <<CMD
  - Running: run_curl_pod curl-${ID} (POST /v1/completions)
CMD
  fi
  run_curl_pod "curl-$ID" \
    sh -c "curl -sS -X POST 'http://${SVC_HOST}/v1/completions' \
         -H 'accept: application/json' \
         -H 'Content-Type: application/json' \
         -d '$payload'"
  output="$CURL_OUTPUT"
  ret="$CURL_EXIT"
  echo "$output"
  [[ $ret -ne 0 || "$output" != *'{'* ]] && {
    echo "Error: POST /v1/completions failed (exit $ret or no JSON)" >&2; failed=true; }
  echo

  if $failed; then
    echo "Iteration $i encountered errors; exiting." >&2
    exit 1
  fi
done

echo "✅ All 10 iterations succeeded."
