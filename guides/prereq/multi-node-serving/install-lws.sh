#!/usr/bin/env bash
# Install or uninstall the LeaderWorkerSet (LWS) controller.
#
# Usage:
#   ./install-lws.sh          # install
#   ./install-lws.sh delete   # uninstall

set -euo pipefail

LWS_VERSION="${LWS_VERSION:-0.7.0}"
LWS_NAMESPACE="${LWS_NAMESPACE:-lws-system}"

if [[ "${1:-}" == "delete" ]]; then
  echo "Uninstalling LWS ${LWS_VERSION} from namespace ${LWS_NAMESPACE}..."
  helm uninstall lws --namespace "${LWS_NAMESPACE}"
else
  echo "Installing LWS ${LWS_VERSION} into namespace ${LWS_NAMESPACE}..."
  helm install lws oci://registry.k8s.io/lws/charts/lws \
    --version "${LWS_VERSION}" \
    --namespace "${LWS_NAMESPACE}" \
    --create-namespace
fi
