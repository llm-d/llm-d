#!/usr/bin/env bash
# Install LeaderWorkerSet and DisaggregatedSet CRDs for guide dry-run and local dev.
set -euo pipefail

# Pin the alpha DisaggregatedSet CRD to a known LWS commit so CI is reproducible.
# This can move to a release tag once DisaggregatedSet is included in an LWS release.
LWS_VERSION="${LWS_VERSION:-f88b79a53a0e8d810d114387a89454d23ea299bb}"
DISAGGREGATEDSET_CRD_URL="${DISAGGREGATEDSET_CRD_URL:-}"

echo "Installing LeaderWorkerSet CRDs (${LWS_VERSION})..."
kubectl apply --server-side -f \
  "https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/config/crd/bases/leaderworkerset.x-k8s.io_leaderworkersets.yaml"

if [[ -z "${DISAGGREGATEDSET_CRD_URL}" ]]; then
  DISAGGREGATEDSET_CRD_URL="https://raw.githubusercontent.com/kubernetes-sigs/lws/${LWS_VERSION}/disaggregatedset/config/crd/bases/disaggregatedset.x-k8s.io_disaggregatedsets.yaml"
fi

echo "Installing DisaggregatedSet CRD..."
if ! kubectl apply --server-side -f "${DISAGGREGATEDSET_CRD_URL}"; then
  echo "DisaggregatedSet CRD not found at ${DISAGGREGATEDSET_CRD_URL}." >&2
  echo "Set DISAGGREGATEDSET_CRD_URL to a local file path if using an unpublished LWS build." >&2
  exit 1
fi

echo "LWS and DisaggregatedSet CRDs installed."
