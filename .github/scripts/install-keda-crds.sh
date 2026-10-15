#!/bin/bash
# Install KEDA CRDs for CI validation of autoscaling guides.
#
# On real clusters KEDA is installed as an operator. This script installs
# only the CRDs so kubectl dry-run can validate ScaledObject and
# TriggerAuthentication resources without deploying the operator.

set -euo pipefail

KEDA_VERSION=${KEDA_VERSION:-"2.20.0"}

echo "Installing KEDA CRDs (v${KEDA_VERSION})..."
kubectl apply --server-side -f \
  "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}-crds.yaml"

echo "KEDA CRDs installed."
