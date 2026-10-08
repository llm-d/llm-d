#!/usr/bin/env bash
# Shared environment variables for all llm-d guides.
# Source this file in your shell before running guide commands:
#   source ${REPO_ROOT}/guides/env.sh

export REPO_ROOT=${REPO_ROOT:-$(realpath "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null)}

### Release Versions for grabbing CRDs
# The *_URL variables are recomputed on every source; a *_URL value exported
# beforehand is discarded. To pin a release, export the matching *_VERSION.
export GATEWAY_API_VERSION=${GATEWAY_API_VERSION:-latest}
if [[ $GATEWAY_API_VERSION == "latest" ]]; then
  export GATEWAY_API_URL=releases/latest/download
else
  export GATEWAY_API_URL=releases/download/${GATEWAY_API_VERSION}
fi
# Controls which release of Gateway API Inference Extension to grab CRDs from
export GAIE_VERSION=${GAIE_VERSION:-latest}
if [[ $GAIE_VERSION == "latest" ]]; then
  export GAIE_URL=releases/latest/download
else
  export GAIE_URL=releases/download/${GAIE_VERSION}
fi
# Controls which release of llm-d/llm-router to grab CRDs from. Used in flowcontrol guide
export ROUTER_RELEASE_VERSION=${ROUTER_RELEASE_VERSION:-latest}
if [[ $ROUTER_RELEASE_VERSION == "latest" ]]; then
  export ROUTER_RELEASE_URL=releases/latest/download
else
  export ROUTER_RELEASE_URL=releases/download/${ROUTER_RELEASE_VERSION}
fi

### Chart versions and OCI coordinates for router chart
export ROUTER_CHART_VERSION=${ROUTER_CHART_VERSION:-v0}
export ROUTER_STANDALONE_CHART=${ROUTER_STANDALONE_CHART:-oci://ghcr.io/llm-d/charts/llm-d-router-standalone}
export ROUTER_GATEWAY_CHART=${ROUTER_GATEWAY_CHART:-oci://ghcr.io/llm-d/charts/llm-d-router-gateway}

### Container Image coordinates and tag for router chart
export ROUTER_EPP_VERSION=${ROUTER_EPP_VERSION:-main}
export ROUTER_EPP_IMAGE=${ROUTER_EPP_IMAGE:-ghcr.io/llm-d/llm-d-router-endpoint-picker}

### Container image used by guide verification steps
export CURL_TEST_IMAGE=${CURL_TEST_IMAGE:-cfmanteiga/alpine-bash-curl-jq:latest}

### Accelerator / model-server guard
# Guides that declare a `support:` matrix in guide.yaml ship one overlay per
# supported pairing at guides/<guide>/modelserver/<ACCELERATOR_TYPE>/<MODEL_SERVER>/.
# Fail early, with the available choices, instead of letting a later
# `kubectl apply -k` fail on a missing directory.
if [[ -n "${GUIDE_NAME:-}" && -n "${ACCELERATOR_TYPE:-}" && -n "${MODEL_SERVER:-}" && -n "${REPO_ROOT:-}" ]]; then
  _llmd_guide_dir="${REPO_ROOT}/guides/${GUIDE_NAME}"
  if [[ -f "${_llmd_guide_dir}/guide.yaml" ]] && grep -q '^support:' "${_llmd_guide_dir}/guide.yaml" \
      && [[ ! -d "${_llmd_guide_dir}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}" ]]; then
    _llmd_engines=$(cd "${_llmd_guide_dir}/modelserver/${ACCELERATOR_TYPE}" 2>/dev/null \
      && for d in vllm sglang trtllm; do [[ -d "$d" ]] && printf '%s ' "$d"; done)
    echo "error: ${GUIDE_NAME} does not support MODEL_SERVER=${MODEL_SERVER} on ACCELERATOR_TYPE=${ACCELERATOR_TYPE}." >&2
    if [[ -n "${_llmd_engines}" ]]; then
      echo "       supported MODEL_SERVER values on ${ACCELERATOR_TYPE}: ${_llmd_engines}" >&2
    else
      echo "       ${ACCELERATOR_TYPE} has no configuration in this guide; see the support table in guides/${GUIDE_NAME}/README.md" >&2
    fi
    unset _llmd_guide_dir _llmd_engines
    return 1 2>/dev/null || exit 1
  fi
  unset _llmd_guide_dir
fi
