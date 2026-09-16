#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

kubectl() {
  case "${1-} ${2-}" in
    'get gateway') printf '%s\n' 'inference-gateway ClusterIP 10.0.0.1' ;;
    'get services') printf '%s\n' 'inference-gateway ClusterIP 10.0.0.2' ;;
    *) return 1 ;;
  esac
}
export -f kubectl

curl() {
  printf '%s\n' '{"data":[{"id":"served-model"}]}'
}
export -f curl

model_name="$(SCRIPT_DIR="${SCRIPT_DIR}" NAMESPACE=test-ns bash -c '
  source "${SCRIPT_DIR}/prepare-inference.sh" >/dev/null
  printf "%s" "${MODEL_NAME}"
')"

[[ "${model_name}" == "served-model" ]]
