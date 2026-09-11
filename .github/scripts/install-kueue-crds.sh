#!/bin/bash
# Install Kueue CRDs for CI validation of the Kueue-based rebalancing guide.
#
# On real clusters Kueue is installed as an operator (or via its Helm chart).
# This script installs only the CRDs so kubectl dry-run can validate
# ResourceFlavor, ClusterQueue and LocalQueue resources without deploying the
# controller and its webhooks.
#
# Kueue publishes no CRD-only release asset, so the CRDs are rendered from the
# chart's own templates with --show-only.

set -euo pipefail

KUEUE_VERSION=${KUEUE_VERSION:-"0.19.1"}
KUEUE_CHART=${KUEUE_CHART:-"oci://registry.k8s.io/kueue/charts/kueue"}

# Only the kinds the guide's overlays declare, plus Workload, which is what the
# guide's verification commands read.
CRDS=(
  clusterqueues
  localqueues
  resourceflavors
  workloads
)

show_only=()
for crd in "${CRDS[@]}"; do
  show_only+=(-s "templates/crd/kueue.x-k8s.io_${crd}.yaml")
done

echo "Installing Kueue CRDs (v${KUEUE_VERSION})..."
helm template kueue "${KUEUE_CHART}" \
  --version "${KUEUE_VERSION}" \
  -n kueue-system \
  "${show_only[@]}" \
  | kubectl apply --server-side -f -

echo "Kueue CRDs installed."
