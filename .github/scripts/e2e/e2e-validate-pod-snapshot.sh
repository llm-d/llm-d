#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-pod-snapshot.sh
# -----------------------------------------------------------------------------
# Validates the pod-snapshot guide beyond the default smoke test.
#
# The smoke loop only proves the guide's router serves chat and text
# completions. This script checks the snapshot/restore lifecycle itself:
# guides/pod-snapshot/scripts/check_thresholds.py reads Pod 1 (cold start +
# snapshot) and Pod 2 (restore from the snapshot), computes the composite
# alerting metrics from the GKE Pod Snapshots CI/CD Plan, prints a PASS/FAIL
# table, and exits non-zero if any metric violates its threshold.
#
# Expects the deploy from guides/pod-snapshot/scripts/nightly-deploy-gke.sh.
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE   Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID        Model the smoke loop discovered (logged only)
  --engine ENGINE             Model server: vllm or sglang (required)
  -h, --help                  Show help
EOF
}

NAMESPACE="llm-d"
MODEL_ID=""
ENGINE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -m|--model)     MODEL_ID="$2"; shift 2 ;;
    --engine)       ENGINE="$2"; shift 2 ;;
    -h|--help)      show_help; exit 0 ;;
    # Fail, not exit 0 like --help: a mistyped flag in a workflow's
    # e2e_validate_args must not pass the validation step without running it.
    *) echo "Unknown option: $1" >&2; show_help >&2; exit 1 ;;
  esac
done

: "${ENGINE:?--engine (vllm or sglang) is required}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPTS_DIR="${REPO_ROOT}/guides/pod-snapshot/scripts"
# Pre-smoke-loop pod snapshot written by guides/pod-snapshot/scripts/nightly-deploy-gke.sh.
PODS_JSON="${PODS_JSON:-/tmp/pod-snapshot.ci/pods.json}"
# The reusable workflow uploads this directory as a run artifact.
RESULTS_DIR="${RESULTS_DIR:-/tmp/pod-logs-pod-snapshot}"
mkdir -p "${RESULTS_DIR}"

echo "Namespace:    ${NAMESPACE}"
echo "Router model: ${MODEL_ID:-not given}"
echo "Engine:       ${ENGINE}"
echo "Results dir:  ${RESULTS_DIR}"

echo "=== Checking pod-snapshot alerting thresholds ==="
# One process, one set of kubectl calls: check_thresholds.py prints and writes
# the per-phase CSV (same format as the demo's results.csv; a failure there is
# logged, not fatal), then applies the thresholds that decide pass/fail.
exec python3 "${SCRIPTS_DIR}/check_thresholds.py" \
  --namespace "${NAMESPACE}" \
  --engine "${ENGINE}" \
  --pods-json "${PODS_JSON}" \
  --csv "${RESULTS_DIR}/results.csv"
