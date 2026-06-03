#!/usr/bin/env bash
# Run lm-evaluation-harness against a deployed llm-d guide.
#
# Usage:
#   NAMESPACE=<ns> ./run-lm-eval.sh -g <guide-name> [-p <local-port>]
#                                   [-o <output-dir>] [--no-port-forward]
#                                   [-c <config.yaml>]
#
# The guide name resolves to the per-guide config template at
# guides/<guide-name>/lm-eval-templates/guide.yaml. Use -c to point at a
# different config file.
#
# Environment overrides (take precedence over the config file):
#   NAMESPACE        Kubernetes namespace of the deployed guide (required).
#   MODEL            Override the model name from the config.
#   TASKS            Comma-separated lm-eval tasks (override config).
#   NUM_FEWSHOT      Number of few-shot examples (override config).
#   LIMIT            Per-task sample limit (override config; empty = full run).
#   MAX_GEN_TOKS     Maximum generated tokens per request (optional).
#   NUM_CONCURRENT   lm-eval client concurrency (override config).
#   GATEWAY_SVC      Kubernetes service to port-forward (overrides discovery).
#   GATEWAY_LABEL    Gateway label value for service discovery (override config).
#   SERVICE_PORT     Service port to forward (default: 80).
#   BASE_URL         If set, skip port-forward and target this URL directly
#                    (must point at a /v1/completions endpoint).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GUIDES_DIR="${REPO_ROOT}/guides"

GUIDE=""
CONFIG=""
LOCAL_PORT="8000"
OUTPUT_DIR=""
DO_PORT_FORWARD=1

usage() {
  cat <<EOF
Usage: NAMESPACE=<ns> $0 -g <guide-name> [options]

Options:
  -g, --guide <name>      Guide name (config at
                          guides/<name>/lm-eval-templates/guide.yaml)
  -c, --config <file>     Explicit config file (overrides -g resolution)
  -p, --port <port>       Local port for kubectl port-forward (default: 8000)
  -o, --output <dir>      Output directory for lm-eval results
                          (default: ./results/<guide>-<timestamp>)
      --no-port-forward   Do not start kubectl port-forward; use \$BASE_URL
  -h, --help              Show this help

Available guides:
EOF
  for f in "${GUIDES_DIR}"/*/lm-eval-templates/guide.yaml; do
    [ -e "$f" ] || continue
    echo "  - $(basename "$(dirname "$(dirname "$f")")")"
  done
}

require_arg() {
  if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
    echo "ERROR: $1 requires a value" >&2
    usage
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--guide)     require_arg "$@"; GUIDE="$2"; shift 2 ;;
    -c|--config)    require_arg "$@"; CONFIG="$2"; shift 2 ;;
    -p|--port)      require_arg "$@"; LOCAL_PORT="$2"; shift 2 ;;
    -o|--output)    require_arg "$@"; OUTPUT_DIR="$2"; shift 2 ;;
    --no-port-forward) DO_PORT_FORWARD=0; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${GUIDE}" && -z "${CONFIG}" ]]; then
  echo "ERROR: -g/--guide (or -c/--config) is required" >&2
  usage
  exit 1
fi

if [[ -z "${CONFIG}" ]]; then
  CONFIG="${GUIDES_DIR}/${GUIDE}/lm-eval-templates/guide.yaml"
fi
if [[ ! -f "${CONFIG}" ]]; then
  echo "ERROR: config not found: ${CONFIG}" >&2
  usage
  exit 1
fi

# Derive a guide label for default output paths / Standalone service fallback.
if [[ -z "${GUIDE}" ]]; then
  GUIDE="$(basename "$(dirname "$(dirname "${CONFIG}")")")"
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: 'yq' (v4+) not found. See helpers/client-setup/README.md" >&2
  exit 1
fi

# Read a scalar from the config, treating yq's "null" (missing key) as empty.
cfg_get() {
  local v
  v="$(yq "$1" "${CONFIG}")"
  [[ "$v" == "null" ]] && v=""
  printf '%s' "$v"
}

# Config provides defaults; non-empty environment values override them.
# LIMIT is special: an explicitly empty LIMIT means "full run", so distinguish
# "set" (even if empty) from "unset".
MODEL="${MODEL:-$(cfg_get '.endpoint.model')}"
TASKS="${TASKS:-$(yq '.evaluation.tasks | join(",")' "${CONFIG}")}"
NUM_FEWSHOT="${NUM_FEWSHOT:-$(cfg_get '.evaluation.num_fewshot')}"
NUM_FEWSHOT="${NUM_FEWSHOT:-0}"
MAX_GEN_TOKS="${MAX_GEN_TOKS:-$(cfg_get '.evaluation.max_gen_toks')}"
NUM_CONCURRENT="${NUM_CONCURRENT:-$(cfg_get '.evaluation.num_concurrent')}"
NUM_CONCURRENT="${NUM_CONCURRENT:-4}"
GATEWAY_LABEL="${GATEWAY_LABEL:-$(cfg_get '.endpoint.gateway_label')}"
GATEWAY_SVC="${GATEWAY_SVC:-}"
SERVICE_PORT="${SERVICE_PORT:-$(cfg_get '.endpoint.service_port')}"
SERVICE_PORT="${SERVICE_PORT:-80}"

if [[ -n "${LIMIT+x}" ]]; then
  LIMIT="${LIMIT}"
else
  LIMIT="$(cfg_get '.evaluation.limit')"
fi

if [[ -z "${MODEL}" ]]; then
  echo "ERROR: model not set in config (.endpoint.model) or MODEL env" >&2
  exit 1
fi
if [[ -z "${TASKS}" ]]; then
  echo "ERROR: tasks not set in config (.evaluation.tasks) or TASKS env" >&2
  exit 1
fi

if ! command -v lm_eval >/dev/null 2>&1; then
  echo "ERROR: 'lm_eval' not found. Install with: pip install 'lm-eval[api]' transformers" >&2
  exit 1
fi

PF_PID=""
cleanup() {
  if [[ -n "${PF_PID}" ]] && kill -0 "${PF_PID}" 2>/dev/null; then
    kill "${PF_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ -z "${BASE_URL:-}" ]]; then
  if [[ "${DO_PORT_FORWARD}" -ne 1 ]]; then
    echo "ERROR: --no-port-forward requires BASE_URL to be set" >&2
    exit 1
  fi

  : "${NAMESPACE:?NAMESPACE env var is required}"

  if ! command -v kubectl >/dev/null 2>&1; then
    echo "ERROR: kubectl not found" >&2
    exit 1
  fi

  if [[ -z "${GATEWAY_SVC}" && -n "${GATEWAY_LABEL}" ]]; then
    GATEWAY_SVC="$(kubectl get svc -n "${NAMESPACE}" \
      -l "gateway.networking.k8s.io/gateway-name=${GATEWAY_LABEL}" \
      --no-headers -o=custom-columns=:metadata.name | head -1)"
  fi

  # Guide READMEs deploy Standalone Mode by default, where the routable service
  # is named <guide>-epp and has no Gateway label. Fall back to that convention
  # so users can deploy from a guide README and run lm_eval directly.
  if [[ -z "${GATEWAY_SVC}" ]] && kubectl get svc -n "${NAMESPACE}" "${GUIDE}-epp" >/dev/null 2>&1; then
    GATEWAY_SVC="${GUIDE}-epp"
  fi

  if [[ -z "${GATEWAY_SVC}" ]]; then
    echo "ERROR: could not find a service to port-forward in ns=${NAMESPACE}" >&2
    echo "       Set GATEWAY_SVC explicitly, deploy Standalone Mode service ${GUIDE}-epp," >&2
    echo "       or set GATEWAY_LABEL to a gateway.networking.k8s.io/gateway-name value." >&2
    exit 1
  fi

  echo "Port-forwarding svc/${GATEWAY_SVC}:${SERVICE_PORT} -> 127.0.0.1:${LOCAL_PORT} (ns=${NAMESPACE})"
  kubectl -n "${NAMESPACE}" port-forward "svc/${GATEWAY_SVC}" "${LOCAL_PORT}:${SERVICE_PORT}" >/dev/null 2>&1 &
  PF_PID=$!

  # Wait until the port is accepting connections (max ~15s).
  PORT_READY=0
  for _ in $(seq 1 30); do
    if ! kill -0 "${PF_PID}" 2>/dev/null; then
      echo "ERROR: kubectl port-forward exited before opening the port" >&2
      exit 1
    fi
    if (echo >"/dev/tcp/127.0.0.1/${LOCAL_PORT}") >/dev/null 2>&1; then
      PORT_READY=1
      break
    fi
    sleep 0.5
  done

  if [[ "${PORT_READY}" -ne 1 ]]; then
    echo "ERROR: timed out waiting for 127.0.0.1:${LOCAL_PORT}" >&2
    exit 1
  fi

  BASE_URL="http://127.0.0.1:${LOCAL_PORT}/v1/completions"
fi

if [[ -z "${OUTPUT_DIR}" ]]; then
  OUTPUT_DIR="./results/${GUIDE}-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "${OUTPUT_DIR}"

MODEL_ARGS="base_url=${BASE_URL},model=${MODEL},tokenizer_backend=huggingface,tokenized_requests=False,num_concurrent=${NUM_CONCURRENT},max_retries=3"
if [[ -n "${MAX_GEN_TOKS}" ]]; then
  MODEL_ARGS="${MODEL_ARGS},max_gen_toks=${MAX_GEN_TOKS}"
fi

LIMIT_ARG=()
if [[ -n "${LIMIT}" ]]; then
  LIMIT_ARG=(--limit "${LIMIT}")
fi

echo "Running lm_eval:"
echo "  guide:        ${GUIDE}"
echo "  base_url:     ${BASE_URL}"
echo "  model:        ${MODEL}"
echo "  tasks:        ${TASKS}"
echo "  num_fewshot:  ${NUM_FEWSHOT}"
echo "  limit:        ${LIMIT:-<full>}"
echo "  max_gen_toks: ${MAX_GEN_TOKS:-<default>}"
echo "  concurrency:  ${NUM_CONCURRENT}"
echo "  output:       ${OUTPUT_DIR}"
echo

lm_eval \
  --model local-completions \
  --model_args "${MODEL_ARGS}" \
  --tasks "${TASKS}" \
  --num_fewshot "${NUM_FEWSHOT}" \
  --batch_size 1 \
  "${LIMIT_ARG[@]}" \
  --output_path "${OUTPUT_DIR}"

echo
echo "Done. Results: ${OUTPUT_DIR}"
