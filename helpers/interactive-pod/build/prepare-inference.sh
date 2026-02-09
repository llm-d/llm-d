#!/bin/bash

# runtime script, requires kubectl, jq

VERBOSE=0

usage() {
  cat <<'EOF'
Usage:
  prepare-inference.sh [OPTIONS] [--] [ARGS...]

Options:
  -v, --verbose   Enable verbose output (prints extra logs to stderr).
  -h, --help      Show this help and exit.

Notes:
  • You can pass -- to stop option parsing and treat the rest as positional args.
  • Verbose mode is off by default. Use -v/--verbose to turn it on.
Examples:
  prepare-inference.sh -v
EOF
}

# Simple logger that respects -v/--verbose
logv() { (( VERBOSE )) && printf '[verbose] %s\n' "$*" >&2; }

# Parse flags: short (-v, -h) and a few long ones via the '-' catch
# Add new short options to the optstring, and new long options in the inner case.
while getopts ':vh-:' opt; do
  case "$opt" in
    v) VERBOSE=1 ;;
    h) usage; exit 0 ;;
    -)
      case "${OPTARG}" in
        verbose) VERBOSE=1 ;;
        help) usage; exit 0 ;;
        *)
          printf 'Unknown option --%s\n' "${OPTARG}" >&2
          usage; exit 2
          ;;
      esac
      ;;
    \?)
      printf 'Unknown option -%s\n' "$OPTARG" >&2
      usage; exit 2
      ;;
  esac
done
shift $((OPTIND - 1))

# ---- Your script logic below ----
logv "Verbose mode is ON"

# Example work:
if (( VERBOSE )); then
    set -x
fi

NAMESPACE="${NAMESPACE:-$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace 2>/dev/null || echo default)}"
GATEWAY_OBJECT=$(kubectl get gateway --no-headers | grep "inference-gateway")
GATEWAY_SERVICE=$(kubectl get services --no-headers | grep "inference-gateway" | awk '{print $1}')

if [[ -z "${GATEWAY_OBJECT}" ]]; then
    echo "Error, could not find the Gateway"
    exit 1
fi

if [[ -z "${GATEWAY_SERVICE}" ]]; then
    echo "Error, could not find the Gateway service"
    exit 1
fi

GATEWAY_NAME=$(echo "${GATEWAY_OBJECT}" | awk '{print $1}')
GATEWAY_ADDRESS=$(echo "${GATEWAY_OBJECT}" | awk '{print $3}')

GATEWAY_SERVICE_ENDPOINT="http://${GATEWAY_SERVICE}.${NAMESPACE}.svc.cluster.local"

MODELS_ENDPOINT_CURL=$(curl "${GATEWAY_SERVICE_ENDPOINT}/v1/models")
MODELS_ENDPOINT_CURL_STATUS=$?

if [[ "${MODELS_ENDPOINT_CURL_STATUS}" != 0 ]]; then
    echo "vLLM server is not up!"
    exit 1

else
    MODEL_NAME=$(echo "${MODELS_ENDPOINT_CURL}" | jq '.data[0].id' )
    if [[ -z "${MODEL_NAME}" || "${MODEL_NAME}" == "null"  ]]; then
        echo "Could not discover model name from vLLM server"
        exit 1
    fi
fi

echo "Successfully curled the gateway! The following values have been discovered:"
export GATEWAY_NAME GATEWAY_ADDRESS GATEWAY_SERVICE_ENDPOINT MODEL_NAME
echo "GATEWAY_NAME: ${GATEWAY_NAME}"
echo "GATEWAY_ADDRESS: ${GATEWAY_ADDRESS}"
echo "GATEWAY_SERVICE_ENDPOINT: ${GATEWAY_SERVICE_ENDPOINT}"
echo "MODEL_NAME: ${MODEL_NAME}"
