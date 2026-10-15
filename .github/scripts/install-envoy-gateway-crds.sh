#!/bin/bash
# Install Envoy Gateway CRDs for CI validation of the envoy-ai-gateway recipe.
#
# On real clusters Envoy Gateway is installed as an operator. This script
# installs only the CRDs so kubectl dry-run can validate resources like
# ClientTrafficPolicy without deploying the controller.

set -euo pipefail

ENVOY_GATEWAY_VERSION=${ENVOY_GATEWAY_VERSION:-"v1.8.3"}

echo "Installing Envoy Gateway CRDs (${ENVOY_GATEWAY_VERSION})..."
helm template eg \
  oci://docker.io/envoyproxy/gateway-crds-helm \
  --version "${ENVOY_GATEWAY_VERSION}" \
  --set crds.gatewayAPI.enabled=false \
  --set crds.envoyGateway.enabled=true \
  | kubectl apply --server-side -f -

echo "Envoy Gateway CRDs installed."
