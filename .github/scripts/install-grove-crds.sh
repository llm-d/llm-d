#!/usr/bin/env bash
# Install the Grove PodCliqueSet CRD for CI validation of the wide-ep Grove
# overlays (guides/wide-ep/modelserver/gpu/vllm/grove/).
#
# On real clusters Grove is installed with its Helm chart, which also runs the
# operator and its webhooks. This script installs only the CRD so kubectl
# dry-run can validate PodCliqueSet resources.

set -euo pipefail

GROVE_VERSION=${GROVE_VERSION:-"v0.1.0-alpha.17"}

echo "Installing Grove PodCliqueSet CRD (${GROVE_VERSION})..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/ai-dynamo/grove/${GROVE_VERSION}/operator/api/core/v1alpha1/crds/grove.io_podcliquesets.yaml"

echo "Grove CRDs installed."
