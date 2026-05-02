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
  --extra-validate NAME       After the smoke loop, hand off to e2e-validate-NAME.sh
                              for guide-specific validation (e.g. NAME=predicted-latency
                              runs e2e-validate-predicted-latency.sh).
  -h, --help                  Show this help and exit
EOF
  exit 0
}

# ── Defaults ────────────────────────────────────────────────────────────────
NAMESPACE="llm-d"
CLI_MODEL_ID=""
VERBOSE=false
EXTRA_VALIDATE=""

# ── Flag parsing ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace)      NAMESPACE="$2"; shift 2 ;;
    -m|--model)          CLI_MODEL_ID="$2"; shift 2 ;;
    -v|--verbose)        VERBOSE=true; shift ;;
    --extra-validate)    EXTRA_VALIDATE="$2"; shift 2 ;;
    -h|--help)           show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

if [[ "${VERBOSE}" == "true" ]]; then
  set -x
fi

# ── Persistent curl pod ─────────────────────────────────────────────────────
# Use a single long-running curl pod for all requests instead of creating a new
# pod per request. This avoids repeated pod scheduling, image pulls, and DNS
# resolution — which previously caused intermittent "Could not resolve host"
# failures (curl exit 6) under load.
CURL_POD_NAME="curl-e2e-${RANDOM}-$$"
CURL_POD_TIMEOUT_SECONDS="${CURL_POD_TIMEOUT_SECONDS:-120}"

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
trap cleanup_curl_pod EXIT

# ── Run curl via kubectl exec on the persistent pod ─────────────────────────
# Usage: run_curl <args…>
# Sets:  CURL_OUTPUT  — captured stdout
#        CURL_EXIT    — curl exit code (0 = success)
run_curl() {
  CURL_OUTPUT=""
  CURL_EXIT=0
  CURL_OUTPUT=$(kubectl exec -n "$NAMESPACE" "$CURL_POD_NAME" -- "$@" 2>&1) || CURL_EXIT=$?
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

# ── Create persistent curl pod ──────────────────────────────────────────────
setup_curl_pod

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
    run_curl curl -sS --max-time 15 "http://${SVC_HOST}/v1/models"
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
  run_curl curl -sS --max-time 120 --retry 2 --retry-delay 5 \
    -X POST "http://${SVC_HOST}/v1/chat/completions" \
    -H 'accept: application/json' \
    -H 'Content-Type: application/json' \
    -d "$chat_payload"
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
  run_curl curl -sS --max-time 120 --retry 2 --retry-delay 5 \
    -X POST "http://${SVC_HOST}/v1/completions" \
    -H 'accept: application/json' \
    -H 'Content-Type: application/json' \
    -d "$payload"
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

# ── Optional: hand off to a guide-specific validator ────────────────────────
# When --extra-validate NAME is set, exec into ${SCRIPT_DIR}/e2e-validate-NAME.sh
# so guides can layer their own checks on top of the standard smoke loop. We
# release the smoke-test curl pod first (the dispatched script creates its
# own) and clear the EXIT trap before exec'ing.
if [[ -n "$EXTRA_VALIDATE" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  EXTRA_SCRIPT="${SCRIPT_DIR}/e2e-validate-${EXTRA_VALIDATE}.sh"
  if [[ ! -x "$EXTRA_SCRIPT" ]]; then
    echo "Error: --extra-validate '${EXTRA_VALIDATE}' but ${EXTRA_SCRIPT} is not executable" >&2
    exit 1
  fi
  echo
  echo "=== Running extra validation: ${EXTRA_VALIDATE} ==="
  cleanup_curl_pod
  trap - EXIT
  exec "$EXTRA_SCRIPT" -n "$NAMESPACE" -m "$MODEL_ID"
fi
