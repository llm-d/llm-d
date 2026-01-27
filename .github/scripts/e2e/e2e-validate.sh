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
  ID=$(gen_id)
  ret=0
  MODEL_ID=$(kubectl run --pod-running-timeout 5m --rm -i curl-discover-${ID} \
                --namespace "$NAMESPACE" \
                --image=curlimages/curl --restart=Never -- \
                curl -sS --max-time 15 "http://${SVC_HOST}/v1/models" | \
                grep -o '"id":"[^"]*"' | \
                head -n 1 | \
                cut -d '"' -f 4) || ret=$?

  if [[ $ret -ne 0 || -z "$MODEL_ID" ]]; then
    echo "Error: Failed to auto-discover model ID from gateway (exit code $ret)." >&2
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
  - Running command:
    kubectl run --rm -i curl-${ID} \\
      --namespace "${NAMESPACE}" \\
      --image=curlimages/curl --restart=Never -- \\
      curl -sS -X POST "http://${SVC_HOST}/v1/chat/completions" \\
        -H 'accept: application/json' \\
        -H 'Content-Type: application/json' \\
        -d '${chat_payload//\'/\'}'
CMD
  fi
  ret=0
  output=$(kubectl run --rm -i curl-"$ID" \
            --namespace "$NAMESPACE" \
            --image=curlimages/curl --restart=Never -- \
            sh -c "sleep 1; curl -sS -X POST 'http://${SVC_HOST}/v1/chat/completions' \
                 -H 'accept: application/json' \
                 -H 'Content-Type: application/json' \
                 -d '$chat_payload'") || ret=$?
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
  - Running command:
    kubectl run --rm -i curl-${ID} \\
      --namespace "${NAMESPACE}" \\
      --image=curlimages/curl --restart=Never -- \\
      curl -sS -X POST "http://${SVC_HOST}/v1/completions" \\
        -H 'accept: application/json' \\
        -H 'Content-Type: application/json' \\
        -d '${payload//\'/\'}'

CMD
  fi
  ret=0
  output=$(kubectl run --rm -i curl-"$ID" \
            --namespace "$NAMESPACE" \
            --image=curlimages/curl --restart=Never -- \
            sh -c "sleep 1; curl -sS -X POST 'http://${SVC_HOST}/v1/completions' \
                 -H 'accept: application/json' \
                 -H 'Content-Type: application/json' \
                 -d '$payload'") || ret=$?
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
