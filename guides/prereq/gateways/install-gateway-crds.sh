#!/usr/bin/env bash
# Install or delete Gateway API and Gateway API Inference Extension CRDs.

set -euo pipefail

if ! command -v kubectl >/dev/null 2>&1; then
  echo "This script depends on kubectl. Please install it." >&2
  exit 1
fi

MODE=${1:-apply}
if [[ "${MODE}" != "apply" && "${MODE}" != "delete" ]]; then
  echo "Unrecognized mode: ${MODE}. Use 'apply' or 'delete'." >&2
  exit 1
fi

GATEWAY_API_VERSION=${GATEWAY_API_VERSION:-v1.5.1}
GAIE_VERSION=${GAIE_VERSION:-v1.5.0}

kubectl "${MODE}" -k "https://github.com/kubernetes-sigs/gateway-api/config/crd?ref=${GATEWAY_API_VERSION}"
kubectl "${MODE}" -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
