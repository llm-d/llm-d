#!/bin/bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# This is a script to automate installation and removal of the Gateway API and Gateway API Inference Extension CRDs

set +x
set -e
set -o pipefail

if [ -z "$(command -v kubectl)" ]; then
  echo "This script depends on \`kubectl\`. Please install it."
  exit 1
fi

# Logging functions and ASCII colour helpers.
COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_RED=$'\e[31m'

log_success() {
  echo "${COLOR_GREEN}✅ $*${COLOR_RESET}"
}

log_error() {
  echo "${COLOR_RED}❌ $*${COLOR_RESET}" >&2
}

## Populate manifests
MODE=${1:-apply} # allowed values "apply" or "delete"
if [[ "$MODE" == "apply" ]]; then
  LOG_ACTION_NAME="Installing"
elif [[ "$MODE" == "delete" ]]; then
  LOG_ACTION_NAME="Deleting"
else
  log_error "Unrecognized Mode: ${MODE}, only supports \`apply\` or \`delete\`."
  exit 1
fi

GATEWAY_API_VERSION=${GATEWAY_API_VERSION:-"v1.5.1"}
### Base CRDs (standard GA APIs only)
log_success "📜 Base CRDs: ${LOG_ACTION_NAME}..."
kubectl $MODE -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml || true


GATEWAY_API_INFERENCE_EXTENSION_VERSION=${GATEWAY_API_INFERENCE_EXTENSION_VERSION:-"v1.5.0"}
### GAIE CRDs
log_success "🚪 GAIE CRDs: ${LOG_ACTION_NAME}..."
kubectl $MODE -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GATEWAY_API_INFERENCE_EXTENSION_VERSION}/v1-manifests.yaml || true
