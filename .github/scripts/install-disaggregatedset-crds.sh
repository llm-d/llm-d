#!/usr/bin/env bash
# Install LeaderWorkerSet and DisaggregatedSet CRDs for guide dry-run and local dev.
set -euo pipefail

# LWS ships the DisaggregatedSet CRD alongside LeaderWorkerSet in config/crd/bases.
# Guides target v0.11.0. Slices, placementPolicy, and per-role scaling arrived in v0.10.0.
LWS_VERSION="${LWS_VERSION:-v0.11.0}"

echo "Installing LeaderWorkerSet CRDs (${LWS_VERSION})..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/config/crd/bases/leaderworkerset.x-k8s.io_leaderworkersets.yaml"

echo "Installing DisaggregatedSet CRD..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/config/crd/bases/disaggregatedset.x-k8s.io_disaggregatedsets.yaml"

echo "LWS and DisaggregatedSet CRDs installed."
