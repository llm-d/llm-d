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

# ── Shared functions ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURL_POD_NAME="curl-e2e-${RANDOM}-$$"
# shellcheck source=lib-e2e-common.sh
source "${SCRIPT_DIR}/lib-e2e-common.sh"
trap cleanup_curl_pod EXIT

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
discover_model

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
if [[ -n "$EXTRA_VALIDATE" ]]; then
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
