#!/bin/bash
# Install Fast Model Actuation (FMA) CRDs for CI validation.
#
# On real clusters these CRDs are installed by the FMA Helm chart.
# This script installs only the CRDs so kubectl dry-run can validate
# FMA resources without deploying the operator.

set -euo pipefail

FMA_VERSION=${FMA_VERSION:-"0.6.4"}
FMA_CRD_BASE="https://raw.githubusercontent.com/llm-d-incubation/llm-d-fast-model-actuation/v${FMA_VERSION}/config/crd"

for crd in \
  fma.llm-d.ai_inferenceserverconfigs.yaml \
  fma.llm-d.ai_launcherconfigs.yaml \
  fma.llm-d.ai_launcherpopulationpolicies.yaml; do
  echo "Installing FMA CRD: $crd"
  kubectl apply --server-side -f "${FMA_CRD_BASE}/${crd}"
done

echo "FMA CRDs installed."
