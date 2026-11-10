#!/usr/bin/env bash
set -Eeuo pipefail

# Run the deployed configuration's offloading gate from the guide YAML.
# Nightly callers opt in after Standup and before their benchmark workload.

show_help() {
  cat <<EOF
Usage: $(basename "$0") -n NAMESPACE -m MODEL_ID

Options:
  -n, --namespace NAMESPACE   Namespace containing the deployed guide (required)
  -m, --model MODEL_ID        Model discovered during Standup (required)
  --accelerator-type TYPE     Guide accelerator selection
  --model-server ENGINE       Model server (vllm or sglang)
  --connector CONNECTOR       Offloading implementation
  --variant TIER              Offload tier (cpu or fs)
  -h, --help                 Show help

Selections default to the nightly's LLMDBENCH_CICD_* environment, then the
guide's ACCELERATOR_TYPE / MODEL_SERVER / CONNECTOR / VARIANT environment,
then guide.yaml defaults.
EOF
}

fail_argument() {
  printf 'error: %s\n' "$1" >&2
  show_help >&2
  exit 2
}

NAMESPACE=""
MODEL_ID=""
ACCELERATOR_SELECTION="${LLMDBENCH_CICD_ACCELERATOR:-${ACCELERATOR_TYPE:-}}"
ENGINE_SELECTION="${LLMDBENCH_CICD_BACKEND:-${MODEL_SERVER:-}}"
CONNECTOR_SELECTION="${LLMDBENCH_CICD_CONNECTOR:-${CONNECTOR:-}}"
TIER_SELECTION="${LLMDBENCH_CICD_OFFLOADING_TARGET:-${VARIANT:-}}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace)
      [[ $# -ge 2 && -n "$2" && "$2" != -* ]] \
        || fail_argument "$1 requires a namespace"
      NAMESPACE="$2"
      shift 2
      ;;
    --accelerator-type|--model-server|--connector|--variant)
      [[ $# -ge 2 && -n "$2" && "$2" != -* ]] \
        || fail_argument "$1 requires a value"
      case "$1" in
        --accelerator-type) ACCELERATOR_SELECTION="$2" ;;
        --model-server) ENGINE_SELECTION="$2" ;;
        --connector) CONNECTOR_SELECTION="$2" ;;
        --variant) TIER_SELECTION="$2" ;;
      esac
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

# Accept the accelerator aliases used by nightly callers.
case "$ACCELERATOR_SELECTION" in
  rocm) ACCELERATOR_SELECTION=amd ;;
  tpu-v5p|tpu-v6|tpu-v7) ACCELERATOR_SELECTION="${ACCELERATOR_SELECTION/tpu-/tpu/}" ;;
esac

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
printf 'set -Eeuo pipefail\nLLMD_TIERED_PREFIX_CACHE_VALIDATED=0\nexport LLMD_TIERED_PREFIX_CACHE_DEADLINE=%s\n' \
  "$(( $(date +%s) + 270 ))" > "$VERIFY_SCRIPT"

GUIDE_VARS=()
[[ -z "$ACCELERATOR_SELECTION" ]] || GUIDE_VARS+=(--var "ACCELERATOR_TYPE=${ACCELERATOR_SELECTION}")
[[ -z "$ENGINE_SELECTION" ]] || GUIDE_VARS+=(--var "MODEL_SERVER=${ENGINE_SELECTION}")
[[ -z "$CONNECTOR_SELECTION" ]] || GUIDE_VARS+=(--var "CONNECTOR=${CONNECTOR_SELECTION}")
[[ -z "$TIER_SELECTION" ]] || GUIDE_VARS+=(--var "VARIANT=${TIER_SELECTION}")

# The README-parsing Standup skips this gate in its "ci" context. Only this
# explicitly selected validation context runs it.
python3 "${REPO_ROOT}/scripts/guide.py" emit "${REPO_ROOT}/guides/tiered-prefix-cache" \
  --context e2e-validation \
  --var "REPO_ROOT=${REPO_ROOT}" \
  --var "GUIDE_NAME=tiered-prefix-cache" \
  --var "NAMESPACE=${NAMESPACE}" \
  --var "MODEL=${MODEL_ID}" \
  "${GUIDE_VARS[@]}" \
  env verify.tests.offload_metrics verify.tests.offload_gate >> "$VERIFY_SCRIPT"

cat <<'BASH' >> "$VERIFY_SCRIPT"
[[ "${LLMD_TIERED_PREFIX_CACHE_VALIDATED}" == "1" ]] || {
  echo "error: the tiered-prefix-cache gate did not complete; missing or filtered verification cannot pass" >&2
  exit 1
}
BASH

printf '=== Validating tiered prefix cache: namespace=%s model=%s ===\n' \
  "$NAMESPACE" "$MODEL_ID"
# timeout owns only this generated program and its child processes.
timeout --signal=TERM --kill-after=5s 280s bash "$VERIFY_SCRIPT" &
VALIDATION_PID=$!
validation_status=0
wait "$VALIDATION_PID" || validation_status=$?
VALIDATION_PID=""
exit "$validation_status"
