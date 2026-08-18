#!/usr/bin/env bash
# Shared environment variables for all llm-d guides.
# Source this file in your shell before running guide commands:
#   source ${REPO_ROOT}/guides/env.sh

export REPO_ROOT=${REPO_ROOT:-$(realpath "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null)}

### Release Versions for grabbing CRDs
export GATEWAY_API_VERSION=${GATEWAY_API_VERSION:-v1.5.1}
if [[ $GATEWAY_API_VERSION == "latest" ]]; then
  export GATEWAY_API_URL=releases/latest/download
else
  export GATEWAY_API_URL=releases/download/${GATEWAY_API_VERSION}
fi
# Controls which release of Gateway API Inference Extension to grab CRDs from
export GAIE_VERSION=${GAIE_VERSION:-v1.5.0}
if [[ $GAIE_VERSION == "latest" ]]; then
  export GAIE_URL=releases/latest/download
else
  export GAIE_URL=releases/download/${GAIE_VERSION}
fi
# Controls which release of llm-d/llm-router to grab CRDs from. Used in flowcontrol guide
export ROUTER_RELEASE_VERSION=${ROUTER_RELEASE_VERSION:-v0.10.0}
if [[ $ROUTER_RELEASE_VERSION == "latest" ]]; then
  export ROUTER_RELEASE_URL=releases/latest/download
else
  export ROUTER_RELEASE_URL=releases/download/${ROUTER_RELEASE_VERSION}
fi

### Chart versions and OCI coordinates for router chart
export ROUTER_CHART_VERSION=${ROUTER_CHART_VERSION:-v0.10.0}
export ROUTER_STANDALONE_CHART=${ROUTER_STANDALONE_CHART:-oci://ghcr.io/llm-d/charts/llm-d-router-standalone}
export ROUTER_GATEWAY_CHART=${ROUTER_GATEWAY_CHART:-oci://ghcr.io/llm-d/charts/llm-d-router-gateway}

### Container Image coordinates and tag for router chart
export ROUTER_EPP_VERSION=${ROUTER_EPP_VERSION:-v0.10.0}
export ROUTER_EPP_IMAGE=${ROUTER_EPP_IMAGE:-ghcr.io/llm-d/llm-d-router-endpoint-picker}
