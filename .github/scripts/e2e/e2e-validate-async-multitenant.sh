#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-async-multitenant.sh
# -----------------------------------------------------------------------------
# Validates the multitenant async-processing guide beyond the default smoke
# test. The smoke loop proves the router answers; this lane has to prove that
# realtime traffic keeps its TTFT, latency and throughput while llm-d-async
# holds a backlog against the same pool, and that llm-d-async alone drives the
# pool to saturation.
#
# All of the work lives in async-multitenant/run.py (an llm-d-benchmark
# inference-perf experiment plus the comparison); this wrapper only exists
# because e2e-validate.sh hands off to an executable named
# e2e-validate-<name>.sh with `-n NAMESPACE -m MODEL_ID`.
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE   Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID        Model to query. If unset, run.py auto-discovers.
  -v, --verbose               Verbose mode
  --dry-run                   One short baseline group only (harness smoke)
  --set KEY=VALUE             Validator setting (AMT_* or LLMDBENCH_*), repeatable
  -h, --help                  Show help
EOF
  exit 0
}

NAMESPACE="llm-d"
MODEL_ID=""
VERBOSE=false
DRY_RUN=false
SETTINGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -m|--model)     MODEL_ID="$2"; shift 2 ;;
    -v|--verbose)   VERBOSE=true; shift ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --set)          SETTINGS+=(--set "$2"); shift 2 ;;
    -h|--help)      show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARGS=(--namespace "$NAMESPACE")
[[ -n "$MODEL_ID" ]] && ARGS+=(--model "$MODEL_ID")
[[ "$VERBOSE" == "true" ]] && ARGS+=(--verbose)
[[ "$DRY_RUN" == "true" ]] && ARGS+=(--dry-run)
[[ ${#SETTINGS[@]} -gt 0 ]] && ARGS+=("${SETTINGS[@]}")

exec python3 "${SCRIPT_DIR}/async-multitenant/run.py" "${ARGS[@]}"
