#!/usr/bin/env bash
# Shared environment variables for all llm-d guides.
# Source this file in your shell before running guide commands:
#   source ${REPO_ROOT}/guides/env.sh

export REPO_ROOT=${REPO_ROOT:-$(realpath $(git rev-parse --show-toplevel 2>/dev/null) 2>/dev/null)}
export GAIE_VERSION=v1.5.0
export ROUTER_CHART_VERSION=v0
export ROUTER_STANDALONE_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev
export ROUTER_GATEWAY_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev
