#!/usr/bin/env bash
# Install LeaderWorkerSet and DisaggregatedSet CRDs for guide dry-run and local dev.
set -euo pipefail

# LWS 0.9.0+ ships DisaggregatedSet CRD alongside LeaderWorkerSet in config/crd/bases.
LWS_VERSION="${LWS_VERSION:-v0.9.0}"

echo "Installing LeaderWorkerSet CRDs (${LWS_VERSION})..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/config/crd/bases/leaderworkerset.x-k8s.io_leaderworkersets.yaml"

echo "Installing DisaggregatedSet CRD..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/config/crd/bases/disaggregatedset.x-k8s.io_disaggregatedsets.yaml"

echo "LWS and DisaggregatedSet CRDs installed."
