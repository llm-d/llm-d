#!/bin/bash
# Install agentgateway CRDs for CI validation of the agentgateway gateway recipes.
#
# On real clusters agentgateway is installed as a controller. This script
# installs only the CRDs so kubectl dry-run can validate AgentgatewayParameters
# resources without deploying the controller.

set -euo pipefail

AGENTGATEWAY_VERSION=${AGENTGATEWAY_VERSION:-"v1.4.1"}

echo "Installing agentgateway CRDs (${AGENTGATEWAY_VERSION})..."
helm upgrade -i agentgateway-crds \
  oci://cr.agentgateway.dev/charts/agentgateway-crds \
  --create-namespace \
  --namespace agentgateway-system \
  --version "${AGENTGATEWAY_VERSION}"

echo "Agentgateway CRDs installed."
