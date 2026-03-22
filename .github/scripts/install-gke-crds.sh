#!/bin/bash
# Install GKE-specific CRDs for CI validation of GKE-targeted helm templates.
#
# On real GKE clusters these CRDs are pre-installed, so this script is only
# needed in CI environments (e.g. kind) where they are absent.

set -euo pipefail

# GKE managed-prometheus CRDs (PodMonitoring, ClusterPodMonitoring, etc.)
GMP_CRD_BASE="https://raw.githubusercontent.com/GoogleCloudPlatform/prometheus-engine/main/charts/operator/crds"

for crd in \
  monitoring.googleapis.com_podmonitorings.yaml \
  monitoring.googleapis.com_clusterpodmonitorings.yaml \
  monitoring.googleapis.com_clusterrules.yaml \
  monitoring.googleapis.com_rules.yaml; do
  echo "Installing GKE monitoring CRD: $crd"
  kubectl apply --server-side --validate=false -f "${GMP_CRD_BASE}/${crd}"
done

# GKE networking CRDs (HealthCheckPolicy, GCPBackendPolicy)
GKE_NET_CRD_BASE="https://raw.githubusercontent.com/GoogleCloudPlatform/gke-networking-recipes/main/gateway-api/config/servicepolicies/crd/standard"

for crd in \
  healthcheckpolicy.yaml \
  gcpbackendpolicies.yaml; do
  echo "Installing GKE networking CRD: $crd"
  kubectl apply --server-side --validate=false -f "${GKE_NET_CRD_BASE}/${crd}"
done

echo "GKE CRDs installed."
