#!/usr/bin/env bash
# Run a per-scenario verify.py locally against results already on disk.
#
# Bridges the gap between "CI sets every LLMDBENCH_* env var" and a laptop
# where you have a benchmark results dir and want to try the same checks.
# Only LLMDBENCH_WORKSPACE is required; everything else is inferred or
# defaulted with a `<local>` sentinel that reads clearly in the output.
# Any scenario-specific env vars (e.g. LLMDBENCH_CICD_OFFLOADING_TARGET) 
# are still required to be set in your shell if the scenario's verify.py expects them.
# 
# Usage:
#   ./verify-locally.sh <scenario> [<workspace-dir>]
#
# Examples:
#   ./verify-locally.sh tiered-prefix-cache
#     -> uses $LLMDBENCH_WORKSPACE from your shell
#
#   ./verify-locally.sh tiered-prefix-cache ~/llmdbenchmark
#     -> uses the given dir as the workspace
#
# Override any auto-inferred value by exporting it in your shell before
# invoking this script:
#   LLMDBENCH_CICD_NS=my-ns LLMDBENCH_CICD_OFFLOADING_TARGET=fs ./verify-locally.sh tiered-prefix-cache

set -euo pipefail

SCENARIO="${1:-}"
if [[ -z "$SCENARIO" || "$SCENARIO" == "-h" || "$SCENARIO" == "--help" ]]; then
  sed -n '2,23p' "$0" | sed -E 's/^# ?//'
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SCRIPT="$SCRIPT_DIR/$SCENARIO/verify.py"
if [[ ! -f "$VERIFY_SCRIPT" ]]; then
  echo "error: no verify.py at $VERIFY_SCRIPT" >&2
  echo "       existing scenarios:" >&2
  for f in "$SCRIPT_DIR"/*/verify.py; do
    [[ -f "$f" && "$f" != *"/_template/"* ]] && echo "         $(basename "$(dirname "$f")")" >&2
  done
  exit 2
fi

# --- Required: workspace ----------------------------------------------------
export LLMDBENCH_WORKSPACE="${2:-${LLMDBENCH_WORKSPACE:-}}"
if [[ -z "$LLMDBENCH_WORKSPACE" ]]; then
  echo "error: LLMDBENCH_WORKSPACE is unset and no workspace dir was passed." >&2
  echo "       Either export it or pass it as the second argument." >&2
  exit 2
fi
if [[ ! -d "$LLMDBENCH_WORKSPACE" ]]; then
  echo "error: LLMDBENCH_WORKSPACE=$LLMDBENCH_WORKSPACE is not a directory." >&2
  exit 2
fi

# --- Namespace: env var, else current kubectl context's default -------------
if [[ -z "${LLMDBENCH_CICD_NS:-}" ]]; then
  LLMDBENCH_CICD_NS="$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null || true)"
  export LLMDBENCH_CICD_NS="${LLMDBENCH_CICD_NS:-default}"
fi

# --- Scenario name derives from directory unless already set ----------------
export LLMDBENCH_CICD_SCENARIO="${LLMDBENCH_CICD_SCENARIO:-$SCENARIO}"

# --- Optional context (only affects the printed header) ---------------------
export LLMDBENCH_CICD_WORKLOAD="${LLMDBENCH_CICD_WORKLOAD:-<local>}"
export LLMDBENCH_CICD_HARNESS="${LLMDBENCH_CICD_HARNESS:-<local>}"
export LLMDBENCH_CICD_DETECTED_MODEL="${LLMDBENCH_CICD_DETECTED_MODEL:-<local>}"
export GITHUB_RUN_ID="${GITHUB_RUN_ID:-local}"

echo "==> Running $SCENARIO/verify.py"
# Print every LLMDBENCH_* var currently in the environment (post-defaulting)
# so it's obvious what the verify.py will see.
env | grep -E '(^LLMDBENCH_)|(^GITHUB_RUN_ID)' | sort | column -t -s= | sed 's/^/    /'

exec python3 "$VERIFY_SCRIPT"
