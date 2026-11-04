#!/usr/bin/env bash
set -Eeuo pipefail

# Run the XPU/vLLM/in-process LMCache CPU gate from the guide YAML.
# The benchmark nightly calls this after Standup and before its workload.

show_help() {
  cat <<EOF
Usage: $(basename "$0") -n NAMESPACE -m MODEL_ID

Options:
  -n, --namespace NAMESPACE   Namespace containing the deployed guide (required)
  -m, --model MODEL_ID        Model discovered during Standup (required)
  -h, --help                 Show help
EOF
}

fail_argument() {
  printf 'error: %s\n' "$1" >&2
  show_help >&2
  exit 2
}

NAMESPACE=""
MODEL_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace)
      [[ $# -ge 2 && -n "$2" && "$2" != -* ]] \
        || fail_argument "$1 requires a namespace"
      NAMESPACE="$2"
      shift 2
      ;;
    -m|--model)
      [[ $# -ge 2 && -n "$2" && "$2" != -* ]] \
        || fail_argument "$1 requires a model"
      MODEL_ID="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      fail_argument "Unknown argument: $1"
      ;;
  esac
done
[[ -n "$NAMESPACE" ]] || fail_argument "Namespace is required"
[[ -n "$MODEL_ID" ]] || fail_argument "Model is required"

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERIFY_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/tiered-prefix-cache-verify.XXXXXX")"
VALIDATION_PID=""
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$VALIDATION_PID" ]]; then
    kill -TERM "$VALIDATION_PID" 2>/dev/null || true
    wait "$VALIDATION_PID" 2>/dev/null || true
  fi
  rm -f -- "$VERIFY_SCRIPT" || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Initialize in the same Bash process as the emitted gate. An inherited value
# must never turn a missing or when-filtered gate into a successful validation.
# Leave time for process cleanup within the workflow's five-minute step limit.
printf 'set -Eeuo pipefail\nLLMD_XPU_LMCACHE_KV_VALIDATED=0\nexport LLMD_XPU_LMCACHE_KV_DEADLINE=%s\n' \
  "$(( $(date +%s) + 270 ))" > "$VERIFY_SCRIPT"

python3 "${REPO_ROOT}/scripts/guide.py" emit "${REPO_ROOT}/guides/tiered-prefix-cache" \
  --context ci \
  --var "REPO_ROOT=${REPO_ROOT}" \
  --var "GUIDE_NAME=tiered-prefix-cache" \
  --var "ACCELERATOR_TYPE=xpu" \
  --var "MODEL_SERVER=vllm" \
  --var "CONNECTOR=lmcache-connector" \
  --var "VARIANT=cpu" \
  --var "NAMESPACE=${NAMESPACE}" \
  --var "MODEL=${MODEL_ID}" \
  env verify.tests.offload_metrics verify.tests.offload_gate >> "$VERIFY_SCRIPT"

cat <<'BASH' >> "$VERIFY_SCRIPT"
[[ "${LLMD_XPU_LMCACHE_KV_VALIDATED}" == "1" ]] || {
  echo "error: the XPU LMCache KV-transfer gate did not complete; missing or filtered verification cannot pass" >&2
  exit 1
}
BASH

printf '=== Validating XPU LMCache transfers: namespace=%s model=%s ===\n' \
  "$NAMESPACE" "$MODEL_ID"
# timeout owns only this generated program and its child processes.
timeout --signal=TERM --kill-after=5s 280s bash "$VERIFY_SCRIPT" &
VALIDATION_PID=$!
validation_status=0
wait "$VALIDATION_PID" || validation_status=$?
VALIDATION_PID=""
exit "$validation_status"
