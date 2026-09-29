#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

kubectl() {
  case "$*" in
    'get gateway -n target-ns --no-headers') printf '%s\n' 'inference-gateway ClusterIP 10.0.0.1' ;;
    'get services -n target-ns --no-headers') printf '%s\n' 'inference-gateway ClusterIP 10.0.0.2' ;;
    *)
      printf 'unexpected kubectl arguments: %s\n' "$*" >&2
      return 1
      ;;
  esac
}
export -f kubectl

curl() {
  printf '%s\n' '{"data":[{"id":"served-model"}]}'
}
export -f curl

result="$(SCRIPT_DIR="${SCRIPT_DIR}" NAMESPACE=target-ns bash -c '
  source "${SCRIPT_DIR}/prepare-inference.sh" >/dev/null
  printf "%s|%s" "${GATEWAY_NAME}" "${GATEWAY_SERVICE_ENDPOINT}"
')"

[[ "${result}" == "inference-gateway|http://inference-gateway.target-ns.svc.cluster.local" ]]
