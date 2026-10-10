#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# e2e-validate-agentic-api.sh
# -----------------------------------------------------------------------------
# Validates the agentic-api guide beyond the default smoke test.
#
# The smoke loop only proves the base guide's router serves chat and text
# completions. This script runs the guide's own verification, the verify
# section of guides/agentic-api/guide.yaml emitted by scripts/guide.py: a
# port-forward to Service/agentic-api and guides/agentic-api/verify.py, which
# checks health and model discovery, stateful /v1/responses continuation from
# PostgreSQL, the webhook tool loop (function_call -> function_call_output) and
# WebSocket /v1/responses.
#
# Expects the deploy from guides/agentic-api/scripts/nightly-deploy-gke.sh:
# Standalone Mode on top of optimized-baseline.
# -----------------------------------------------------------------------------

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE   Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID        Model the smoke loop discovered (logged only; verify.py
                              discovers the model through agentic-api's /v1/models)
  -h, --help                  Show help
EOF
  exit 0
}

NAMESPACE="llm-d"
MODEL_ID=""
# Must match BASE_GUIDE_NAME in guides/agentic-api/scripts/nightly-deploy-gke.sh.
BASE_GUIDE_NAME="optimized-baseline"

while [[ $# -gt 0 ]]; do
  case $1 in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -m|--model)     MODEL_ID="$2"; shift 2 ;;
    -h|--help)      show_help ;;
    *) echo "Unknown option: $1"; show_help ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERIFY_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/agentic-api-verify.XXXXXX")"
trap 'rm -f "${VERIFY_SCRIPT}"' EXIT

echo "Namespace:  ${NAMESPACE}"
echo "Base guide: ${BASE_GUIDE_NAME} (router model: ${MODEL_ID:-not given})"

python3 "${REPO_ROOT}/scripts/guide.py" emit "${REPO_ROOT}/guides/agentic-api" \
  --context ci \
  --var REPO_ROOT="${REPO_ROOT}" \
  --var NAMESPACE="${NAMESPACE}" \
  --var BASE_GUIDE_NAME="${BASE_GUIDE_NAME}" \
  env verify > "${VERIFY_SCRIPT}"

echo "=== Running the agentic-api guide verification ==="
bash "${VERIFY_SCRIPT}"
