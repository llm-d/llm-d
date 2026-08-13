#!/bin/bash
# Install NVIDIA DRA driver CRDs for CI validation of wide-ep-lws OCI overlays.
#
# On real GB200/NVLink clusters these CRDs are installed by the NVIDIA DRA
# driver. This script installs only the ComputeDomain CRD so kubectl dry-run
# can validate resources that reference it without deploying the driver.

set -euo pipefail

NVIDIA_DRA_VERSION=${NVIDIA_DRA_VERSION:-"v25.8.0"}
NVIDIA_DRA_CRD_BASE="https://raw.githubusercontent.com/NVIDIA/k8s-dra-driver-gpu/refs/tags/${NVIDIA_DRA_VERSION}/deployments/helm/nvidia-dra-driver-gpu/crds"

echo "Installing NVIDIA DRA ComputeDomain CRD (${NVIDIA_DRA_VERSION})..."
kubectl apply --server-side -f \
  "${NVIDIA_DRA_CRD_BASE}/resource.nvidia.com_computedomains.yaml"

echo "NVIDIA DRA CRDs installed."
